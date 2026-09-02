// run_go: the Go host, over the cgo binding. Runs every program in a hostset
// directory through liblypning and logs each one to the capture log.
//
//	usage: run_go <hostset-dir>
//
// Same walk as the C, C++, Rust, Node and Python drivers, and no fall-onward
// for the same reason: this counts what the subset itself takes, and a driver
// that quietly answered from CPython would report a coverage the subset does
// not have. A refusal is counted, logged with the exit code it carries, and
// the run carries on (docs/EMBEDDING.md §1).
//
// It logs each run to $LYPNING_LOG in the shim's own record shape. A library
// call spawns no interpreter, so neither of lypning's capture feeds can see
// it; see study/hosts/capture.h for why that is the host's job.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"lypning.dev/lypning"
)

var (
	logPath = os.Getenv("LYPNING_LOG")
	session = os.Getenv("LYPNING_STUDY_SESSION")
)

// One python_invocation record, field for field the shim's own, so that
// `lypning harvest` merges it. Only `shim` differs: it names the host.
type record struct {
	Kind      string   `json:"kind"`
	Ts        string   `json:"ts"`
	Session   *string  `json:"session"`
	Shim      string   `json:"shim"`
	Pid       int      `json:"pid"`
	Program   string   `json:"program"`
	Module    *string  `json:"module"`
	Script    *string  `json:"script"`
	ArgvTail  []string `json:"argv_tail"`
	StdinPipe bool     `json:"stdin_pipe"`
	StdinKind string   `json:"stdin_kind"`
	ExitCode  int      `json:"exit_code"`
	WallMs    int64    `json:"wall_ms"`
}

func capture(host, program string, args []string, exitCode int, wallMs int64) {
	if logPath == "" {
		return
	}
	rec := record{
		Kind:      "python_invocation",
		Ts:        time.Now().UTC().Format("2006-01-02T15:04:05Z"),
		Shim:      host,
		Pid:       os.Getpid(),
		Program:   program,
		ArgvTail:  args,
		StdinPipe: true,
		StdinKind: "bytes",
		ExitCode:  exitCode,
		WallMs:    wallMs,
	}
	if session != "" {
		rec.Session = &session
	}
	if args == nil {
		rec.ArgvTail = []string{}
	}
	// Best-effort, exactly like the shim: a lost sighting, never a failed run.
	fh, err := os.OpenFile(logPath, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o644)
	if err != nil {
		return
	}
	defer fh.Close()
	enc := json.NewEncoder(fh)
	// Not HTML: `<` in a program stays `<`, as it does in every other host's log.
	enc.SetEscapeHTML(false)
	enc.Encode(rec)
}

func main() {
	if len(os.Args) < 2 {
		os.Stderr.WriteString("usage: run_go <hostset-dir>\n")
		os.Exit(2)
	}
	root := os.Args[1]
	entries, err := os.ReadDir(root)
	if err != nil {
		os.Stderr.WriteString("run_go: " + err.Error() + "\n")
		os.Exit(1)
	}
	var names []string
	for _, e := range entries {
		if e.IsDir() {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)

	ran, refused, other, n := 0, 0, 0, 0
	for _, name := range names {
		dir := filepath.Join(root, name)
		src, err := os.ReadFile(filepath.Join(dir, "program.py"))
		if err != nil {
			continue
		}
		n++
		stdin, _ := os.ReadFile(filepath.Join(dir, "stdin"))
		var args []string
		if raw, err := os.ReadFile(filepath.Join(dir, "args")); err == nil {
			for _, a := range strings.Split(string(raw), "\n") {
				if a = strings.TrimRight(a, "\r"); a != "" {
					args = append(args, a)
				}
			}
		}

		// The program runs in THIS process; give it the entry directory, where
		// prepare.py put the fixtures it was written against.
		home, _ := os.Getwd()
		moved := os.Chdir(dir) == nil
		t0 := time.Now()
		r := lypning.Run(string(src), &lypning.Options{
			Args: args, Stdin: stdin, StepLimit: 200000000, OutputLimit: 1 << 20,
		})
		ms := time.Since(t0).Milliseconds()
		if moved && home != "" {
			os.Chdir(home)
		}

		switch r.Status {
		case lypning.OK:
			ran++
		case lypning.Unsupported:
			refused++
		default:
			other++
		}
		capture("go-embed", string(src), args, r.ExitCode, ms)
	}
	fmt.Printf("%-12s %d programs: %d ran, %d refused, %d other\n", "go-embed", n, ran, refused, other)
}
