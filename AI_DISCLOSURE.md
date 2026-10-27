# AI Use and Prompt-History Summary

AI coding assistance was used to scaffold and implement this project. The candidate is responsible for reviewing and understanding the submitted code.

## Prompt-history record

The interaction included a request to build a complete, runnable solution for a synthetic returns workflow challenge, including relational entities, policy decisions, labelled anomalies, deterministic generation, exports, documentation, tests, and reproducibility. Follow-up requests supplied the challenge context and asked for a stronger working implementation.

The original assessment prompt and candidate email are intentionally not copied into this repository. The email accompanying the assessment says that the assessment content must not be shared, copied, recorded, or published. This summary records the substance of the prompts without reproducing restricted wording.

## Where AI helped

- Proposed a standard-library Python/SQLite CLI layout and seeded generation approach.
- Drafted schema, policy, generator, validation, documentation, and test code.
- Helped identify and correct generation/validation inconsistencies while implementing.

## Candidate review and validation

The candidate should run the documented starter generation, validator, reproducibility command, and unit tests; inspect the schema, policy, exports, and labelled cases; and be prepared to explain or change the submitted code. The repository documentation identifies policy assumptions and limitations rather than presenting the demo as a production integration.

## Verification record

Verified locally with Python 3.13.3:

- `python -m unittest discover -s tests -v`: 6 tests passed.
- Generated starter exports: 300 orders, 50 return requests, 80 return lines; all 16 validation checks passed.
- Generated full exports with seed `20261022`: 30,000 orders, 5,000 return requests, 8,000 return lines; all 16 validation checks passed.
- `python -m returns_workflow verify-reproducibility --scale full --seed 20261022`: reproducible; all 17 checksummed files matched across two generated datasets.

The generated full output is included under `data/full`; the smaller reviewer-friendly sample is under `data/starter`.
