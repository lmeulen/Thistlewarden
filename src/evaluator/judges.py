"""Compatibility exports for judge profiles and built-in judge behavior.

New code imports :mod:`evaluator.judging` modules.
"""

from evaluator.judging.builtin import (
    OpenAICompatibleJudge,
    build_judge_prompt,
    parse_judge_output,
    register_builtin_judge,
    resolve_rubric,
)
from evaluator.judging.profiles import (
    JudgeProfileInput,
    JudgeProfilePatch,
    JudgeProfileRecord,
    JudgeProfileService,
    SQLiteJudgeProfileRepository,
    judge_profile_to_dict,
)

__all__ = [
    "JudgeProfileInput",
    "JudgeProfilePatch",
    "JudgeProfileRecord",
    "JudgeProfileService",
    "OpenAICompatibleJudge",
    "SQLiteJudgeProfileRepository",
    "build_judge_prompt",
    "judge_profile_to_dict",
    "parse_judge_output",
    "register_builtin_judge",
    "resolve_rubric",
]
