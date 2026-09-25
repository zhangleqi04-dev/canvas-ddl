"""Source policy in code: live Canvas outranks validated document evidence."""
from dataclasses import replace
from datetime import timezone
from .models import SourceConflict
from .deduplicator import title_key, PRIORITY


class DeadlineReconciler:
    @staticmethod
    def value_repr(d):
        return d.actionable_at.date().isoformat() if d.date_only else d.actionable_at.isoformat()

    @staticmethod
    def logical_key(d):
        return d.course_id, title_key(d.title), d.type

    @staticmethod
    def compatible(a, b):
        if a.date_only or b.date_only:
            return a.actionable_at.date() == b.actionable_at.date()
        return a.actionable_at.astimezone(timezone.utc) == b.actionable_at.astimezone(timezone.utc)

    def reconcile(self, records):
        undated = [d for d in records if d.source_type != "official_document" and not d.actionable_at]
        canvas = [d for d in records if d.source_type != "official_document" and d.actionable_at]
        documents = [d for d in records if d.source_type == "official_document"]
        warnings = []
        complete = True
        groups = {}
        for d in documents:
            groups.setdefault(self.logical_key(d), []).append(d)
        retained = []
        for key, docs in groups.items():
            removed = [d for d in undated if self.logical_key(d) == key]
            matches = [i for i, d in enumerate(canvas) if self.logical_key(d) == key]
            if removed and not matches:
                complete = False
                warnings.append(f"Course {key[0]}: live Canvas has no due date for {docs[0].title}; document timing withheld pending review.")
                retained.extend(replace(d, reconciliation_status="unresolved_conflict", canonical_reason="live_canvas_unscheduled",
                                        sources=tuple(dict.fromkeys(d.sources + tuple(s for c in removed for s in c.sources))),
                                        conflicts=(SourceConflict("actionable_at", None, self.value_repr(d), d.sources[0],
                                                                  "live_canvas_unscheduled"),)) for d in docs)
                continue
            anchors = {}
            for i in matches:
                d = canvas[i]
                direct = next((v for v in d.identities if v.startswith("assignment:")), None)
                anchor = direct or f"{d.source_type}:{d.canvas_resource_id}"
                anchors.setdefault(anchor, []).append(i)
            # Calendar records with the identical value may be extra views of a
            # single Canvas item. Distinct same-source IDs never collapse here.
            primary = [a for a, ids in anchors.items() if any(canvas[i].source_type != "canvas_calendar_event" for i in ids)]
            for a in list(anchors):
                ids = anchors[a]
                if a in primary or not all(canvas[i].source_type == "canvas_calendar_event" for i in ids):
                    continue
                linked = [p for p in primary if all(self.compatible(canvas[i], canvas[anchors[p][0]]) for i in ids)]
                if len(linked) == 1:
                    anchors[linked[0]].extend(anchors.pop(a))
            repeat_instances = any(len({d.actionable_at for d in docs if d.sources[0].document_id == doc_id}) > 1
                                   for doc_id in {d.sources[0].document_id for d in docs})
            if len(anchors) == 1 and not repeat_instances:
                ids = next(iter(anchors.values()))
                preferred = min((canvas[i] for i in ids), key=lambda d: (not d.authoritative_dates, PRIORITY.get(d.source_type, 9)))
                token = f"reconciled:{preferred.deadline_id}"
                all_sources = tuple(dict.fromkeys(preferred.sources + tuple(s for d in docs for s in d.sources)))
                conflicts = list(preferred.conflicts)
                for doc in docs:
                    if not self.compatible(preferred, doc):
                        conflicts.append(SourceConflict("actionable_at", self.value_repr(preferred), self.value_repr(doc),
                                                        doc.sources[0], "live_canvas_preferred"))
                        warnings.append(f"{preferred.course_code}: {preferred.title} has conflicting document timing; live Canvas value selected.")
                for i in ids:
                    old = canvas[i]
                    canvas[i] = replace(old, start_at=preferred.start_at, due_at=preferred.due_at, end_at=preferred.end_at,
                                        date_only=preferred.date_only, all_day_date=preferred.all_day_date,
                                        sources=tuple(dict.fromkeys(old.sources + all_sources)),
                                        identities=tuple(dict.fromkeys(old.identities + tuple(v for doc in docs for v in doc.identities) + (token,))), conflicts=tuple(dict.fromkeys(conflicts)),
                                        reconciliation_status="resolved_conflict" if conflicts else "agreed",
                                        canonical_reason="live_canvas_preferred")
            elif matches:
                complete = False
                warnings.append(f"Course {key[0]}: document item {docs[0].title} matches multiple Canvas items; reconciliation needs review.")
                retained.extend(replace(d, reconciliation_status="ambiguous", canonical_reason="identity_requires_review") for d in docs)
            else:
                source_ids = {d.sources[0].document_id for d in docs}
                same_value = all(self.compatible(a, b) for i, a in enumerate(docs) for b in docs[i+1:])
                if len(source_ids) > 1 and not same_value:
                    complete = False
                    warnings.append(f"Course {key[0]}: official documents disagree on {docs[0].title}; no canonical date selected.")
                    # Preserve evidence on unresolved items, but never count them.
                    retained.extend(replace(d, reconciliation_status="unresolved_conflict", canonical_reason="document_conflict_requires_review",
                                             conflicts=tuple(SourceConflict("actionable_at", None, self.value_repr(o), o.sources[0],
                                                                            "unresolved_document_conflict") for o in docs if o != d)) for d in docs)
                elif same_value:
                    preferred = min(docs, key=lambda d: (d.date_only, d.deadline_id))
                    token = f"reconciled:{preferred.deadline_id}"
                    retained.extend(replace(preferred, sources=tuple(dict.fromkeys(preferred.sources + tuple(s for other in docs for s in other.sources))),
                                            identities=tuple(dict.fromkeys(v for other in docs for v in other.identities)) + (token,),
                                            reconciliation_status="agreed" if len(docs) > 1 else "single_source") for _ in docs)
                else:
                    retained.extend(docs)  # Repeated dates in one document are distinct instances.
        return canvas + retained, tuple(warnings), complete
