#!/usr/bin/env python3
"""ResearchMemory — journal persistant des hypothèses testées par la factory.

Log tested/rejected/survived (JSON), dédup par (venue, famille, params) pour ne
pas re-tester, et surface les survivants accumulés. Permet au générateur de
reprendre une recherche sans refaire le travail.
"""
import json
import os
from typing import Dict, List


class ResearchMemory:
    def __init__(self, path: str) -> None:
        self._path = path
        self._records: Dict[str, dict] = {}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    for r in json.load(f):
                        self._records[self._key(r["hypothesis"],
                                                r.get("venue", "?"))] = r
            except (json.JSONDecodeError, KeyError, OSError):
                self._records = {}

    @staticmethod
    def _key(hypothesis: dict, venue: str) -> str:
        # gère les 2 formats : générateur {"family","params"} et DSL/LLM
        # {"signal":{"type","params"}}.
        sig = hypothesis.get("signal")
        if isinstance(sig, dict):
            typ, params = sig.get("type", "?"), sig.get("params", {})
        else:
            typ, params = hypothesis.get("family", "?"), hypothesis.get("params", {})
        ps = ",".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{venue}|{typ}|{ps}"

    def is_tested(self, hypothesis: dict, venue: str) -> bool:
        return self._key(hypothesis, venue) in self._records

    def record(self, result: dict) -> None:
        venue = result.get("venue", "?")
        self._records[self._key(result["hypothesis"], venue)] = result

    def all(self) -> List[dict]:
        return list(self._records.values())

    def survivors(self) -> List[dict]:
        return [r for r in self._records.values() if r.get("pass")]

    def save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(list(self._records.values()), f, indent=2)
        os.replace(tmp, self._path)  # écriture atomique
