package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	mesh "github.com/thebrazenbeard/vera-mesh/gateway/veramesh-go/gateway"
	"github.com/thebrazenbeard/vera-mesh/gateway/veramesh-go/veraport"
)

func main() {
	version := flag.Bool("version", false, "print VeraPort protocol target")
	listen := flag.String("listen", "127.0.0.1:17446", "loopback HTTP listen address behind the reviewed HTTPS reverse proxy")
	controllerConfig := flag.String("controller-config", "", "path to VeraPort controller config")
	oauthConfig := flag.String("oauth-config", "", "path to OAuth introspection config")
	checkConfig := flag.Bool("check-config", false, "validate configuration and exit without listening or dialing VeraPort")
	flag.Parse()

	if *version {
		fmt.Println(veraport.ProtocolVersion)
		return
	}
	if *controllerConfig == "" || *oauthConfig == "" {
		fmt.Fprintln(os.Stderr, "-controller-config and -oauth-config are required")
		os.Exit(64)
	}
	if err := requireLoopbackListen(*listen); err != nil {
		log.Fatal(err)
	}

	cfg, err := mesh.LoadConfig(*controllerConfig)
	if err != nil {
		log.Fatal(err)
	}
	controller, err := mesh.NewController(cfg)
	if err != nil {
		log.Fatal(err)
	}
	defer controller.Close()

	oauthCfg, err := mesh.LoadIntrospectionConfig(*oauthConfig)
	if err != nil {
		log.Fatal(err)
	}
	verifier, err := mesh.NewIntrospectionVerifier(oauthCfg)
	if err != nil {
		log.Fatal(err)
	}
	public, err := mesh.NewPublicHTTPServer(controller, oauthCfg, verifier.Verify)
	if err != nil {
		log.Fatal(err)
	}
	if *checkConfig {
		fmt.Printf("VeraMesh gateway configuration valid: resource=%s listen=%s\n", public.Resource, *listen)
		return
	}

	httpServer := &http.Server{
		Addr:              *listen,
		Handler:           public.Handler,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    1 << 20,
	}

	errCh := make(chan error, 1)
	go func() {
		log.Printf("VeraMesh MCP gateway listening on loopback %s for public resource %s", *listen, public.Resource)
		errCh <- httpServer.ListenAndServe()
	}()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(sigCh)

	select {
	case sig := <-sigCh:
		log.Printf("shutdown requested: %s", sig)
	case err := <-errCh:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatal(err)
		}
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := httpServer.Shutdown(ctx); err != nil {
		log.Printf("HTTP shutdown incomplete: %v", err)
	}
	if results := public.Gateway.Close(ctx); len(results) != 0 {
		log.Printf("closed %d actor facade(s)", len(results))
	}
}

func requireLoopbackListen(address string) error {
	host, _, err := net.SplitHostPort(address)
	if err != nil {
		return fmt.Errorf("listen address must be host:port: %w", err)
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return fmt.Errorf("public gateway process must bind to a literal loopback IP, got %q", host)
	}
	return nil
}
