"""Entry point for sandboxed module execution (FE-05).

**This runner is deliberately not ACL-gated, and must not become the gate.**

It builds a bare ``Registry`` + ``Executor`` with no ACL attached. That is
safe only because the *parent* reaches the access decision before spawning it
— see ``Sandbox.execute`` and ``acl_loader.enforce_acl_for_unguarded_path``
(FE-14 §4.10). Re-loading the ACL here would be defence in depth at best and
a false control at worst: the sandbox forwards a narrow environment allowlist
by design and runs in a temporary working directory, so a relative
``acl.root`` cannot resolve from here and this process's view of the rule set
is neither guaranteed nor trustworthy. One enforcement point, in the parent,
where the ACL actually is.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> None:
    module_id = sys.argv[1]
    input_data = json.loads(sys.stdin.read())
    extensions_root = os.environ.get("APCORE_EXTENSIONS_ROOT", "./extensions")

    from apcore import Executor, Registry

    registry = Registry(extensions_dir=extensions_root)
    registry.discover()
    executor = Executor(registry)
    result = executor.call(module_id, input_data)
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
