package main

import (
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestHealthz(t *testing.T) {
	rec := httptest.NewRecorder()
	handler(t.TempDir()).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK || rec.Body.String() != "ok\n" {
		t.Fatalf("got %d %q", rec.Code, rec.Body.String())
	}
}

func TestDataCheck(t *testing.T) {
	dir := t.TempDir()
	rec := httptest.NewRecorder()
	handler(dir).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/data-check", nil))
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("missing file: got %d", rec.Code)
	}
	if err := os.WriteFile(filepath.Join(dir, dataFile), []byte("ok\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	rec = httptest.NewRecorder()
	handler(dir).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/data-check", nil))
	if rec.Code != http.StatusOK || rec.Body.String() != "ok\n" {
		t.Fatalf("got %d %q", rec.Code, rec.Body.String())
	}
}

func TestHealthcheck(t *testing.T) {
	ok := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {}))
	defer ok.Close()
	if got := healthcheck(ok.URL); got != 0 {
		t.Fatalf("200: got %d", got)
	}
	bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer bad.Close()
	if got := healthcheck(bad.URL); got != 1 {
		t.Fatalf("503: got %d", got)
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := l.Addr().String()
	l.Close()
	if got := healthcheck("http://" + addr + "/healthz"); got != 1 {
		t.Fatalf("refused: got %d", got)
	}
}
