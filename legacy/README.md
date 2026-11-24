# Legacy / reference-only modules

These files are kept for history. They are **not** part of the importable
`sovereign` package and are not on the production path.

- `sovereign_engine.py` — TCP master/node sovereignty simulation. Official
  SE2 docs call this scaffolding / reference-only. The production loop is
  `sovereign.agent`.
- `kernel.py` — original unsandboxed Jupyter wrapper.
- `kernel_2.py` — original bwrap+seccomp kernel (required `pyseccomp` at
  import/start). Its sandbox features were folded into `sovereign.kernel`
  (optional seccomp, official rlimits kept, IPC socket path fixed).

Do not import these from new code.
