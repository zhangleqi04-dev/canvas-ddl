from dataclasses import dataclass


@dataclass(frozen=True)
class Course:
    course_id: str
    course_code: str
    course_name: str

