package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/thebrazenbeard/vera-mesh/gateway/veramesh-go/veraport"
)

func main() {
	version := flag.Bool("version", false, "print VeraPort protocol target")
	flag.Parse()
	if *version {
		fmt.Println(veraport.ProtocolVersion)
		return
	}
	fmt.Fprintln(
		os.Stderr,
		"VeraMesh Go gateway bootstrap is not yet a runnable MCP service; use -version for build qualification.",
	)
	os.Exit(64)
}
