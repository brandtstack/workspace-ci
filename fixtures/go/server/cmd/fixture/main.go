package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

// dataFile is written at start, so the image check proves /data is writable by the image user.
const dataFile = "fixture-started"

func port() string {
	if p := os.Getenv("PORT"); p != "" {
		return p
	}
	return "8080"
}

func dataDir() string {
	if d := os.Getenv("DATA_DIR"); d != "" {
		return d
	}
	return "/data"
}

func handler(dir string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write([]byte("ok\n"))
	})
	// Fixture only: echoes the file written at start, so fixture-check.sh can assert the write.
	mux.HandleFunc("/data-check", func(w http.ResponseWriter, _ *http.Request) {
		b, err := os.ReadFile(filepath.Join(dir, dataFile))
		if err != nil {
			http.Error(w, "missing", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write(b)
	})
	return mux
}

// healthcheck is the image HEALTHCHECK: GET 127.0.0.1:$PORT/healthz, 2 s timeout, 0 on 200 else 1.
func healthcheck(url string) int {
	c := http.Client{Timeout: 2 * time.Second}
	resp, err := c.Get(url)
	if err != nil {
		fmt.Fprintln(os.Stderr, "healthcheck:", err)
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		fmt.Fprintln(os.Stderr, "healthcheck: status", resp.StatusCode)
		return 1
	}
	return 0
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck("http://127.0.0.1:" + port() + "/healthz"))
	}
	dir := dataDir()
	if err := os.WriteFile(filepath.Join(dir, dataFile), []byte("ok\n"), 0o600); err != nil {
		log.Fatalf("write %s: %v", dir, err)
	}
	log.Fatal(http.ListenAndServe(":"+port(), handler(dir)))
}
