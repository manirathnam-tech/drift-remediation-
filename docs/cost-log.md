# Infrastructure session cost log

`make up` records the start and successful completion time for each session. `make down` records teardown completion and the spend status. AWS Cost Explorer can report charges with a delay. After data appears, run `make cost-note START=YYYY-MM-DD END=YYYY-MM-DD` with END exclusive to append the account total for those dates.

Cost Explorer is account-wide and may include charges outside this project. It cannot give exact per-session attribution when multiple sessions or other activity overlap on a date. Record the reported amount as a date-range account observation and reconcile other activity before treating it as project spend. The S3 Terraform state bucket and Secrets Manager deploy key are retained between sessions.

| Session (UTC) | Started | Ended | Result | Spend |
|---|---|---|---|---|

| 20261022T013427Z | 2026-10-22T01:34:27Z | — | In progress | Pending Cost Explorer |
