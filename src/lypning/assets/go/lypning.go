// Package lypning runs the bottom slice of agent-typed Python, the one-liners,
// inside this Go process: no fork, no exec, no pipe, no serialisation.
// Everything else it REFUSES, and the refusal is the design, not a failure.
//
//	r := lypning.Run(src, &lypning.Options{StepLimit: 10000000}) // no process to kill
//	if r.FallOnward {
//		runOnPython3(src) // lypning ran NOTHING; your existing path, unchanged
//	} else {
//		use(r.Stdout, r.Stderr, r.ExitCode) // bytes; a traceback IS the answer: exit 1
//	}
//
// The one invariant this file holds is that a refusal is a VALUE. Run never
// returns an error, never panics for a program, and never prints; every outcome,
// including "this Go string was not UTF-8", arrives as a Result whose FallOnward
// says whether CPython should answer now. FallOnward is true exactly when
// lypning declined and left nothing behind (nothing printed, no file touched,
// no stdin consumed), which is what makes the retry safe.
//
// # Linking
//
// This is cgo over the unchanged C ABI in ../include/lypning.h, so it needs a C
// compiler and CGO_ENABLED=1. A source checkout links with no environment at all:
// the directives below point at the crate's own release-lib target. Anything
// else says where the library is through CGO_LDFLAGS, whose two shapes are
//
//	CGO_LDFLAGS="-L<checkout>/src/lypning/assets/rust/target/release-lib -Wl,-rpath,<same>"
//	CGO_LDFLAGS="$(lypning lib --libs)"
//
// and a link error naming -llypning means neither is true yet: run
// `lypning build --lib` first. The checkout default costs nothing when the
// directory is absent: GNU ld ignores a -L it cannot find, and macOS ld warns
// "search path not found" and then links exactly as it would have. Neither
// changes which library the binary loads.
//
// # Threads
//
// A run is confined to one OS thread. A cgo call pins its goroutine to the
// thread it is on for the duration of the call, and no other goroutine runs on
// that thread meanwhile, so one Run per goroutine at a time is enough: any
// number of goroutines may call Run concurrently and none can see Busy from
// another. The program sees the process's working directory and environment,
// which are process-wide and not this package's to change.
package lypning

/*
#cgo CFLAGS: -I${SRCDIR}/../include
#cgo LDFLAGS: -L${SRCDIR}/../rust/target/release-lib -Wl,-rpath,${SRCDIR}/../rust/target/release-lib
#cgo LDFLAGS: -llypning
#include <stdlib.h>
#include <lypning.h>
*/
import "C"

import (
	"fmt"
	"sync"
	"unsafe"
)

// Status is what lypning_result_status() answers, one value per outcome.
type Status int32

const (
	// OK: the program ran. ExitCode is its own: 0, or what it gave sys.exit().
	OK Status = C.LYPNING_OK
	// Error: the program raised. Stderr holds the traceback; ExitCode is 1.
	// This IS the program's answer and is never routed onward.
	Error Status = C.LYPNING_ERROR
	// Unsupported: lypning refused. NOT a failure. ExitCode 90, Stdout empty,
	// Stderr exactly one `lypning: unsupported: <kind>: <detail>` line.
	Unsupported Status = C.LYPNING_UNSUPPORTED
	// Busy: this thread was already running a program. Nothing executed.
	// Unreachable from Go (see the package comment) but kept, because the
	// ABI has it and a binding that maps a value it cannot name is a bug.
	Busy Status = C.LYPNING_BUSY
	// Panic: the interpreter itself failed. Report it, and route onward.
	Panic Status = C.LYPNING_PANIC
)

// UnsupportedExit is the exit code of a refusal, everywhere in this project.
const UnsupportedExit = C.LYPNING_UNSUPPORTED_EXIT

func (s Status) String() string {
	switch s {
	case OK:
		return "ok"
	case Error:
		return "error"
	case Unsupported:
		return "unsupported"
	case Busy:
		return "busy"
	case Panic:
		return "panic"
	}
	return fmt.Sprintf("status(%d)", int32(s))
}

// Routing is lypning's own front end answering "which interpreter should run
// this?" after one parse and no execution. See Route.
type Routing struct {
	// Engine is "lypning", "lypning-mp" or "cpython".
	Engine string
	// Kind and Detail name the construct that pushed the program past lypning
	// ("module", "import re"), or are "" when nothing did.
	Kind, Detail string
	// Imports is every module the program imports, sorted and deduplicated:
	// the question is which modules it needs, which has no order. The one
	// import that decided the tier is Detail.
	Imports []string
}

// Options is everything a host decides about a program before running it.
// The zero value is a one-liner with no arguments, an empty stdin, the
// filesystem allowed and no limits. Pass nil for exactly that.
type Options struct {
	// Args becomes sys.argv[1:].
	Args []string
	// Filename becomes sys.argv[0]. Unset gives CPython's `-c` shape.
	Filename string
	// Stdin is the program's whole standard input, as bytes. The library
	// never reads this process's fd 0; unset is an empty stream.
	Stdin []byte
	// DenyFilesystem turns every file operation into a REFUSAL rather than
	// a lie: the program is never told a file is missing, you are told
	// lypning would not run it, and you decide whether CPython gets it.
	DenyFilesystem bool
	// StepLimit refuses once the program has taken this many statements or
	// iterator advances. SET IT for anything a language model wrote: there is
	// no process to kill inside your own thread, so `while True: pass` with
	// no limit is a hang with no way back. It bounds work, not time.
	StepLimit uint64
	// OutputLimit refuses once captured output passes this many bytes,
	// rather than growing a buffer in your address space.
	OutputLimit uint
}

// Result is one run's outcome. It is a value: nothing in it points into C.
type Result struct {
	Status Status
	// ExitCode is what the `lypning` binary would have exited with: the
	// program's own, 1 for an uncaught exception, 90 for a refusal.
	ExitCode int
	// Stdout and Stderr are the program's, as bytes, because its output is
	// whatever it printed. Stdout is empty after a refusal, by the commit
	// barrier; Stderr is then exactly the one refusal line.
	Stdout, Stderr []byte
	// Kind and Detail are the refusal's two halves ("module", "import re"),
	// so a host can branch on the kind without parsing the line. "" when the
	// run was not a refusal.
	Kind, Detail string
	// Committed says the run passed the point where its effects stop being
	// reversible: true for anything that finished, false for a refusal.
	Committed bool
	// FallOnward is THE field to branch on. True for every outcome that is not
	// the program's own answer and left nothing behind: a refusal, a Busy that
	// executed nothing, a Panic that reached no commit. Never true for OK or
	// Error, because an uncaught exception is as much of an answer as a
	// printed line, and re-running it would repeat its side effects.
	FallOnward bool
}

// The ABI is checked once, against the library that actually loaded, and a
// mismatch is a panic rather than a Result: it is not a property of any
// program, it is this binary having been compiled against the wrong header,
// and every answer after it would be a guess.
var abiOnce sync.Once

func checkABI() {
	abiOnce.Do(func() {
		got := uint32(C.lypning_abi_version())
		want := uint32(C.LYPNING_ABI_VERSION)
		if got != want {
			panic(fmt.Sprintf("lypning: liblypning answers ABI version %d but this package was compiled against lypning.h ABI %d; rebuild the library (lypning build --lib) or this binary so they agree", got, want))
		}
	})
}

// Version is the runtime's own, e.g. "0.1.0".
func Version() string {
	checkABI()
	return C.GoString(C.lypning_version())
}

// ABIVersion is what the loaded library answers, which after checkABI is also
// the header's LYPNING_ABI_VERSION.
func ABIVersion() uint32 {
	checkABI()
	return uint32(C.lypning_abi_version())
}

// Route says which interpreter should run src, at the cost of one parse. It is
// lypning's own front end, not a heuristic over the text, so "lypning" means
// lypning can genuinely run it; the refusal from Run is what catches the cases
// only running can tell. Source that is not UTF-8 routes to "cpython".
func Route(src string) Routing {
	checkABI()
	p := C.CString(src)
	defer C.free(unsafe.Pointer(p))
	r := C.lypning_route_new(p, C.size_t(len(src)))
	defer C.lypning_route_free(r)
	if r == nil {
		return Routing{Engine: "cpython", Kind: "source", Detail: "not UTF-8"}
	}
	n := int(C.lypning_route_import_count(r))
	imports := make([]string, 0, n)
	for i := 0; i < n; i++ {
		imports = append(imports, C.GoString(C.lypning_route_import(r, C.size_t(i))))
	}
	return Routing{
		Engine:  C.GoString(C.lypning_route_engine(r)),
		Kind:    C.GoString(C.lypning_route_kind(r)),
		Detail:  C.GoString(C.lypning_route_detail(r)),
		Imports: imports,
	}
}

// onward is the Result for the one thing the ABI will not build a request
// for: bytes that are not UTF-8, in the source or in argv. It has the same
// shape the runtime gives a NUL byte in the source, a refusal of kind
// "source", so a host branches on it like any other, and FallOnward is true
// because CPython would have seen those bytes and has its own message for them.
func onward(detail string) Result {
	return Result{
		Status:     Unsupported,
		ExitCode:   UnsupportedExit,
		Stdout:     []byte{},
		Stderr:     []byte("lypning: unsupported: source: " + detail + "\n"),
		Kind:       "source",
		Detail:     detail,
		FallOnward: true,
	}
}

// Run runs src in this goroutine's thread, capturing its output, and never
// spawns anything. opts may be nil. It never returns an error: a refusal is a
// Result with FallOnward true, and so is the one thing the ABI will not even
// build a request for, a src or an argument that is not UTF-8, because that
// program too still needs an answer and CPython can give it one.
func Run(src string, opts *Options) Result {
	checkABI()
	if opts == nil {
		opts = &Options{}
	}
	p := C.CString(src)
	defer C.free(unsafe.Pointer(p))
	q := C.lypning_request_new(p, C.size_t(len(src)))
	defer C.lypning_request_free(q)
	if q == nil {
		return onward("not UTF-8")
	}
	if opts.Filename != "" {
		f := C.CString(opts.Filename)
		rc := C.lypning_request_set_filename(q, f, C.size_t(len(opts.Filename)))
		C.free(unsafe.Pointer(f))
		if rc != 0 {
			return onward("sys.argv[0] not UTF-8")
		}
	}
	for _, a := range opts.Args {
		c := C.CString(a)
		rc := C.lypning_request_add_arg(q, c, C.size_t(len(a)))
		C.free(unsafe.Pointer(c))
		if rc != 0 {
			// The ABI declines an argument that is not UTF-8. Dropping it and
			// running anyway would run a different program than the one
			// CPython would see, which is exactly the silent disagreement a
			// refusal exists to prevent.
			return onward("argv not UTF-8")
		}
	}
	if len(opts.Stdin) > 0 {
		// The request copies the bytes during this call and retains nothing,
		// which is the whole of what cgo's pointer rules ask for.
		C.lypning_request_set_stdin(q, unsafe.Pointer(&opts.Stdin[0]), C.size_t(len(opts.Stdin)))
	}
	if opts.DenyFilesystem {
		C.lypning_request_set_filesystem(q, 0)
	}
	C.lypning_request_set_step_limit(q, C.uint64_t(opts.StepLimit))
	C.lypning_request_set_output_limit(q, C.size_t(opts.OutputLimit))

	r := C.lypning_run(q)
	defer C.lypning_result_free(r)
	// Every byte and string is copied out here, so the Result outlives the
	// handle that the deferred free is about to take back.
	var n C.size_t
	out := C.lypning_result_stdout(r, &n)
	stdout := C.GoBytes(unsafe.Pointer(out), C.int(n))
	errp := C.lypning_result_stderr(r, &n)
	stderr := C.GoBytes(unsafe.Pointer(errp), C.int(n))
	return Result{
		Status:     Status(C.lypning_result_status(r)),
		ExitCode:   int(C.lypning_result_exit_code(r)),
		Stdout:     stdout,
		Stderr:     stderr,
		Kind:       C.GoString(C.lypning_result_kind(r)),
		Detail:     C.GoString(C.lypning_result_detail(r)),
		Committed:  C.lypning_result_committed(r) != 0,
		FallOnward: C.lypning_result_should_fall_onward(r) != 0,
	}
}

// FallOnward is the dispatcher's own predicate, for a host that chains OTHER
// interpreters too (lypning-mp, or a sandboxed python3) and has only their exit
// code and stderr to go on. True for exit 90, for a MemoryError, and for a
// traceback reported with exit 0; deliberately false for an ordinary non-zero
// exit with a traceback, which is very often the program's own correct answer.
func FallOnward(exitCode int, stderr []byte) bool {
	checkABI()
	var p unsafe.Pointer
	if len(stderr) > 0 {
		p = unsafe.Pointer(&stderr[0])
	}
	return C.lypning_fall_onward(C.int32_t(exitCode), p, C.size_t(len(stderr))) != 0
}
