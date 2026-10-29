"""Stable strict JSON parsing import for shared response-format consumers.

Legacy scoring/record schemas have been retired; panel records live in runner.
"""
from .contract import parse as parse_response

InvalidResponse = ValueError
