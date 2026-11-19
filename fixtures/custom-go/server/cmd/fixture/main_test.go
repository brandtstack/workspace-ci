package main

import (
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCheckKey(t *testing.T) {
	good := base64.StdEncoding.EncodeToString(make([]byte, 32))
	for name, tc := range map[string]struct {
		in      string
		wantErr string
	}{
		"32 bytes":      {good, ""},
		"unset":         {"", "FIXTURE_KEY is not set"},
		"not base64":    {"%%%not base64%%%", "not base64"},
		"16 bytes":      {base64.StdEncoding.EncodeToString(make([]byte, 16)), "decodes to 16 bytes, want 32"},
		"33 bytes":      {base64.StdEncoding.EncodeToString(make([]byte, 33)), "decodes to 33 bytes, want 32"},
		"url-safe form": {strings.NewReplacer("+", "-", "/", "_").Replace(base64.StdEncoding.EncodeToString([]byte{0xfb, 0xff, 0xfe, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29})), "not base64"},
	} {
		err := checkKey(tc.in)
		switch {
		case tc.wantErr == "" && err != nil:
			t.Errorf("%s: unexpected error %v", name, err)
		case tc.wantErr != "" && (err == nil || !strings.Contains(err.Error(), tc.wantErr)):
			t.Errorf("%s: got %v, want an error containing %q", name, err, tc.wantErr)
		}
	}
}

func TestHandler(t *testing.T) {
	h := handler([]string{"one", "two"})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK || rec.Body.String() != "ok\n" {
		t.Fatalf("/healthz: got %d %q", rec.Code, rec.Body.String())
	}
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/dbs", nil))
	if rec.Code != http.StatusOK || rec.Body.String() != "one,two\n" {
		t.Fatalf("/dbs: got %d %q", rec.Code, rec.Body.String())
	}
}

func TestHealthcheck(t *testing.T) {
	ok := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer ok.Close()
	if got := healthcheck(ok.URL); got != 0 {
		t.Fatalf("200: got %d", got)
	}
	bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusServiceUnavailable) }))
	defer bad.Close()
	if got := healthcheck(bad.URL); got != 1 {
		t.Fatalf("503: got %d", got)
	}
}
