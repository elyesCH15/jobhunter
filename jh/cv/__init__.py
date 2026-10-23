"""CV editor back end: career database → CV spec → Typst → PDF / PNG.

career.py    load career/career.yaml (local file or GitHub), look entries / bullets up by id
spec.py      the CV spec: what one CV contains (selection, order, overrides, custom items)
defaults.py  a new spec for the base CV or for a job (by profile)
resolve.py   spec + career → plain document (also the only text a future LLM step may see)
render.py    document → Typst source → PDF / PNG. The only module that reads career/personal.yaml.
yamledit.py  apply one text edit to career.yaml, keeping comments and layout
github.py    read / write career.yaml through the GitHub Contents API ("Save to database")
"""
