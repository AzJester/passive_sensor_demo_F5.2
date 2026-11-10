# Revision A reference code

These files are unchanged copies from the Revision A package. They stay here for two reasons.

1. `triangulate.py` and `test_triangulate.py` keep the original two-bearing utility working. Run it as before:
   `python legacy/triangulate.py examples/node_a.json examples/node_b.json --reference-time 1001`
   New work should use `python -m pasensor fuse`, which adds heading error, node-position error, sound travel time, and three-node consistency checks.
2. `dsp_rev_a.py` is the Revision A estimator. The test suite runs it beside the Revision B estimator on the same independent synthetic signals, so any change in behavior is measured and not assumed.

Do not import these files from new code.
