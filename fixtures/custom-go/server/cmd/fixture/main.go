// Fixture for family: custom + go. Refuses to start unless FIXTURE_KEY is 32 random bytes (base64) and both
// databases named in DB_ONE_URL and DB_TWO_URL answer, which is what the contract's smoke-random and
// smoke-postgres give it in CI.
package main

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

func port() string {
	if p := os.Getenv("PORT"); p != "" {
		return p
	}
	return "8080"
}

// checkKey wants standard base64 that decodes to exactly 32 bytes.
func checkKey(s string) error {
	if s == "" {
		return errors.New("FIXTURE_KEY is not set")
	}
	b, err := base64.StdEncoding.DecodeString(s)
	if err != nil {
		return errors.New("FIXTURE_KEY is not base64")
	}
	if len(b) != 32 {
		return fmt.Errorf("FIXTURE_KEY decodes to %d bytes, want 32", len(b))
	}
	return nil
}

// databaseName connects to url and returns current_database(): it proves the server answers, the credentials
// work and the database exists.
func databaseName(ctx context.Context, url string) (string, error) {
	conn, err := pgx.Connect(ctx, url)
	if err != nil {
		return "", err
	}
	defer conn.Close(ctx)
	var name string
	if err := conn.QueryRow(ctx, "SELECT current_database()").Scan(&name); err != nil {
		return "", err
	}
	return name, nil
}

func handler(dbs []string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write([]byte("ok\n"))
	})
	// Fixture only: the database names the start-up ping saw, so fixture-check.sh can assert both exist.
	mux.HandleFunc("/dbs", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write([]byte(strings.Join(dbs, ",") + "\n"))
	})
	return mux
}

// healthcheck is the image HEALTHCHECK: GET 127.0.0.1:$PORT/healthz, 2 s timeout, 0 on 200 else 1.
func healthcheck(url string) int {
	c := http.Client{
		Timeout:       2 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
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
	if err := checkKey(os.Getenv("FIXTURE_KEY")); err != nil {
		log.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	var dbs []string
	for _, env := range []string{"DB_ONE_URL", "DB_TWO_URL"} {
		url := os.Getenv(env)
		if url == "" {
			log.Fatalf("%s is not set", env)
		}
		name, err := databaseName(ctx, url)
		if err != nil {
			// The URL carries credentials in general: report the variable, never the URL or the driver's text.
			log.Fatalf("%s: database did not answer", env)
		}
		dbs = append(dbs, name)
	}
	log.Printf("listening on :%s (databases %s)", port(), strings.Join(dbs, ","))
	log.Fatal(http.ListenAndServe(":"+port(), handler(dbs)))
}
