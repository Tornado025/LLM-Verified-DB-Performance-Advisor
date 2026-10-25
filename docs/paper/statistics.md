# Statistical method notes

For paired positive baseline/candidate measurements, analyze
`d_i = log(candidate_i) - log(baseline_i)`. The estimated relative change is
`expm1(mean(d))`, a geometric mean ratio, not an arithmetic mean latency ratio.
Inference assumes approximately independent, normally distributed paired log
ratios across runs. Dependence or heavy tails can invalidate nominal error rates;
measurement and noise experiments must assess that limitation.

With at most L planned looks and F simultaneous tests, allocate one-sided
`a = alpha / (L * F)`. Each look uses Student-t bounds with n-1 degrees of freedom
and sample standard error. Bonferroni's union bound controls the probability of
any false acceptance across these tests and looks at alpha, conditional on valid
single-test bounds and a respected look budget. It does not require independence
between looks or simultaneous tests. An sd floor of 1e-6 widens degenerate bounds.

Accept non-inferiority only when the upper relative-change bound is strictly below
delta. Reject when the lower bound exceeds delta; otherwise continue until the
budget is exhausted and BLOCK. Improvement uses a threshold of minus the required
minimum gain. Sequential instances latch terminal decisions and forbid spending
extra looks; callers of the stateless API must enforce their schedule themselves.

The conservative allocation can increase false blocks and validation cost under
noise. Report these costs in RQ2 rather than presenting a low false-apply rate
alone. Synthetic calibration outcomes and seeds are logged in docs/PROGRESS.md;
these simulations are empirical checks under their noise model, not a guarantee
for arbitrary workloads. Real-workload results remain pending.
