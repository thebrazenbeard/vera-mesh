package main

import (
	"os"

	"github.com/thebrazenbeard/vera-mesh/reference/windows-go/internal/operator"
)

func main() {
	os.Exit(operator.Run(os.Stdin, os.Stdout, os.Stderr, os.Args[1:]))
}
