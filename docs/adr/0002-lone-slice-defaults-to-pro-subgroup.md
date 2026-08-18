# Give a lone slice pro by default; split only on a same-run pair

`plan_subgroups()` used to check whether the Campaign Stage's current `param_overrides` already said `subgroup=pro` (`pro_in_use`), and flip a lone new slice to standard whenever that was true. That produced an unwanted pro → standard → pro alternation on every single-slice submission cycle, because `pro_in_use` reflects the *last configured* override, not whether the previous pro submission is still actually running — nothing clears it when a submission finishes. In practice this meant a `production`-role campaign submitting one slice at a time never held pro for more than one cycle in a row.

The constraint that actually matters is narrower: two submissions launched *in the same run* can't both hold the one pro slot. A lone slice, submitted alone, has no same-run rival for it, so it should just take pro. This removed the need to read `param_overrides` back from POMS before deciding — `plan_subgroups(num_slices, role)` is now a pure function of what this run is about to submit, nothing else.

Accepted risk: a new slice is submitted once the *previous* one crosses `pct_complete_threshold`, not once it's `Finished`, so there's a brief window where the old and new slice are both technically `Running` and both requesting pro. Treated as short-lived and not worth guarding against.
