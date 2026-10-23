"""Правила-плагины. Импорт модулей регистрирует их в RULES."""
from . import fall, objects, person  # noqa: F401
from .base import RULES, Rule, RuleContext, Signal, build_rule, register

__all__ = ["RULES", "Rule", "RuleContext", "Signal", "build_rule", "register"]
