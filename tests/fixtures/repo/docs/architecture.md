# Architecture

The domain models live in src/models.py and the transfer flow in
src/service.py.

Use `open_account` to create an account, and `allows()` to check the policy
ceiling before a transfer.

An Account is a plain dataclass — this sentence names it in prose, not in
code, so it is not a link. Neither is src/missing.py, which does not exist.
