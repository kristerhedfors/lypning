module lypning.dev/study/run_go

go 1.21

require lypning.dev/lypning v0.0.0

// The binding lives in this checkout, not in a registry; the replace is what
// keeps this module at zero network fetches, like the binding itself.
replace lypning.dev/lypning => ../../../src/lypning/assets/go
