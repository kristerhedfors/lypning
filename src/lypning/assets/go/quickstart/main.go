// quickstart: the smallest complete lypning host in Go, and the file to copy:
// run the program in this process, or fall onward to CPython.
//
//	cd src/lypning/assets/go && go run ./quickstart "print(sum(range(10)))"
//
// Usage: quickstart "<python source>" [args...]   (args become sys.argv[1:])
package main

import (
	"os"
	"os/exec"

	"lypning.dev/lypning"
)

func main() {
	if len(os.Args) < 2 {
		os.Stderr.WriteString("usage: quickstart \"<python source>\" [args...]\n")
		os.Exit(2)
	}
	src, args := os.Args[1], os.Args[2:]
	// StepLimit: a model wrote this program and it runs on THIS thread. No process to kill.
	r := lypning.Run(src, &lypning.Options{Args: args, StepLimit: 10000000})
	if r.FallOnward {
		// A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
		cmd := exec.Command("python3", append([]string{"-c", src}, args...)...)
		cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
		err := cmd.Run()
		if exit, ok := err.(*exec.ExitError); ok {
			os.Exit(exit.ExitCode())
		}
		if err != nil {
			os.Stderr.WriteString("quickstart: python3: " + err.Error() + "\n")
			os.Exit(127)
		}
		os.Exit(0)
	}
	os.Stdout.Write(r.Stdout)
	os.Stderr.Write(r.Stderr)
	os.Exit(r.ExitCode)
}
