package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/selector"
	"gopkg.in/yaml.v3"
)

func cliFixture(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	for _, name := range []string{"opencode", "pi-web"} {
		dir := filepath.Join(root, "sources", name)
		if err := os.MkdirAll(filepath.Join(dir, "scripts"), 0o755); err != nil {
			t.Fatal(err)
		}
		data := "upstream: {url: 'https://example.test/repo', ref: v1, patch: 0}\n" +
			"targets:\n"
		targets := []string{"npm"}
		if name == "opencode" {
			targets = []string{"linux", "windows"}
		}
		for _, target := range targets {
			data += "  - name: " + target + "\n    steps: [echo ok]\n"
			if name == "opencode" {
				data += "    cache: {paths: ['~/.cache/app'], key_files: ['scripts/**']}\n"
			}
		}
		for file, value := range map[string]string{
			"build.yaml":       data,
			"README.md":        "Description",
			"scripts/build.sh": "echo ok\n",
		} {
			if err := os.WriteFile(filepath.Join(dir, file), []byte(value), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
	dir := filepath.Join(root, "sources", "app")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256([]byte(appAsset))
	data := "upstream:\n  release: {repo: owner/sources, tag: sources-app, " +
		"asset: app.apk, sha256: " + hex.EncodeToString(sum[:]) + "}\n" +
		"  version: 1.2.3\n  patch: 0\n" +
		"release:\n  targets: [{type: repo, repo: bunker}]\n" +
		"targets:\n  - name: android\n    secrets: [SIGN_KEY]\n    steps: [echo ok]\n"
	for file, value := range map[string]string{"build.yaml": data, "README.md": "App"} {
		if err := os.WriteFile(filepath.Join(dir, file), []byte(value), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

// appAsset is the body of the app.apk release asset of the app fixture recipe.
const appAsset = "apk body"

// captureStdout returns everything fn writes to os.Stdout.
func captureStdout(t *testing.T, fn func()) string {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	saved := os.Stdout
	os.Stdout = w
	done := make(chan string)
	go func() {
		data, _ := io.ReadAll(r)
		done <- string(data)
	}()
	defer func() { os.Stdout = saved }()
	fn()
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return <-done
}
func readOutputs(t *testing.T, file string) map[string]string {
	t.Helper()
	data, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]string{}
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		key, value, _ := strings.Cut(line, "=")
		out[key] = value
	}
	return out
}
func Test_prepare(t *testing.T) {
	root := cliFixture(t)
	out := filepath.Join(t.TempDir(), "outputs")
	t.Setenv("GITHUB_OUTPUT", out)
	t.Setenv("GITHUB_REPOSITORY", "owner/repo")
	t.Setenv("GITHUB_REF_NAME", "feature")
	t.Setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
	t.Setenv("GRAVES_SOURCES", "pi-web opencode app")
	t.Setenv("GRAVES_DRY_RUN", "1")
	var err error
	stdout := captureStdout(t, func() { err = prepare(context.Background(), root, true) })
	if err != nil {
		t.Fatal(err)
	}
	values := readOutputs(t, out)
	var matrix selector.Matrix
	if err := json.Unmarshal([]byte(values["matrix"]), &matrix); err != nil {
		t.Fatal(err)
	}
	if _, ok := values["release_id"]; ok || values["mode"] != "draft" ||
		values["has_jobs"] != "true" || values["telegram"] != "false" ||
		len(matrix.Include) != 4 ||
		matrix.Include[0].Source != "app" || matrix.Include[0].CacheKey != "" ||
		matrix.Include[0].ReleaseRepo != "owner/bunker" ||
		fmt.Sprint(matrix.Include[0].Secrets) != "[SIGN_KEY]" ||
		matrix.Include[1].CacheKey == "" ||
		matrix.Include[3].CacheKey != "" {
		t.Fatal(values)
	}
	for _, e := range matrix.Include {
		if e.ReleaseID != 0 || (e.Source != "app" && e.ReleaseRepo != "owner/repo") {
			t.Fatal(values)
		}
	}
	for _, line := range []string{
		"DRY-RUN ensure release owner/bunker v999 draft=true\n",
		"DRY-RUN ensure release owner/repo v999 draft=true\n",
	} {
		if strings.Count(stdout, line) != 1 {
			t.Fatal(stdout)
		}
	}
	t.Setenv("GITHUB_EVENT_NAME", "push")
	t.Setenv("GITHUB_REF_TYPE", "tag")
	t.Setenv("GITHUB_REF_NAME", "pi-web-v1.202609.0p1")
	if err := prepare(context.Background(), root, true); err != nil {
		t.Fatal(err)
	}
	values = readOutputs(t, out)
	if values["mode"] != "release" || values["tag"] != "pi-web-v1.202609.0p1" {
		t.Fatal(values)
	}
	data, err := os.ReadFile(filepath.Join(root, ".graves", "notes", "notes.md"))
	if err != nil || !strings.Contains(string(data), "Generated without LLM") {
		t.Fatalf("%s %v", data, err)
	}
	t.Setenv("GITHUB_REF_NAME", "app-v1.2.3p0")
	stdout = captureStdout(t, func() { err = prepare(context.Background(), root, true) })
	if err != nil || !strings.Contains(stdout,
		"DRY-RUN ensure release owner/bunker app-v1.2.3p0 draft=false\n") ||
		strings.Contains(stdout, "owner/repo") {
		t.Fatalf("%s %v", stdout, err)
	}
	data, err = os.ReadFile(filepath.Join(root, ".graves", "notes", "notes.md"))
	if err != nil || !strings.Contains(string(data),
		"Upstream: owner/sources release sources-app asset app.apk") {
		t.Fatalf("%s %v", data, err)
	}
	t.Setenv("GITHUB_REF_NAME", "nope-v1")
	if err := prepare(context.Background(), root, true); err == nil {
		t.Fatal("bad tag")
	}
	t.Setenv("GITHUB_REF_TYPE", "branch")
	if err := prepare(context.Background(), root, true); err == nil {
		t.Fatal("branch build")
	}
	t.Setenv("GITHUB_EVENT_NAME", "schedule")
	if err := prepare(context.Background(), root, true); err == nil {
		t.Fatal("schedule build")
	}
}
func Test_preparePullRequest(t *testing.T) {
	root := cliFixture(t)
	ctx := context.Background()
	git := func(args ...string) {
		t.Helper()
		if err := ci.RunCommand(
			ctx,
			ci.WithCommand("git", args...),
			ci.WithCommandDir(root),
			ci.WithCommandEnv(
				"GIT_AUTHOR_NAME=Test",
				"GIT_AUTHOR_EMAIL=test@example.test",
				"GIT_COMMITTER_NAME=Test",
				"GIT_COMMITTER_EMAIL=test@example.test",
			),
		); err != nil {
			t.Fatal(err)
		}
	}
	git("init", "-q", "-b", "master")
	git("add", ".")
	git("commit", "-qm", "initial")
	git("update-ref", "refs/remotes/origin/master", "HEAD")
	if err := os.WriteFile(
		filepath.Join(root, "common"),
		[]byte("common code"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	git("add", ".")
	git("commit", "-qm", "common")
	out := filepath.Join(t.TempDir(), "output")
	t.Setenv("GITHUB_EVENT_NAME", "pull_request")
	t.Setenv("GITHUB_BASE_REF", "master")
	t.Setenv("GITHUB_EVENT_PATH", "")
	t.Setenv("GITHUB_OUTPUT", out)
	t.Setenv("GITHUB_REPOSITORY", "owner/repo")
	if err := prepare(ctx, root, true); err != nil {
		t.Fatal(err)
	}
	if values := readOutputs(t, out); values["has_jobs"] != "false" ||
		values["matrix"] != `{"include":[]}` {
		t.Fatal(values)
	}
	if err := os.WriteFile(
		filepath.Join(root, "sources", "pi-web", "README.md"),
		[]byte("changed"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	git("add", ".")
	git("commit", "-qm", "app")
	if err := prepare(ctx, root, true); err != nil {
		t.Fatal(err)
	}
	values := readOutputs(t, out)
	if strings.Count(values["matrix"], `"source"`) != 1 ||
		!strings.Contains(values["matrix"], `"source":"pi-web"`) {
		t.Fatal(values)
	}
}
func Test_executeFetchRelease(t *testing.T) {
	root := cliFixture(t)
	var server *httptest.Server
	handler := func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/repos/owner/sources/releases/tags/sources-app":
			sum := sha256.Sum256([]byte(appAsset))
			_, _ = fmt.Fprintf(w, `{"tag_name":"sources-app","assets":[{"name":"app.apk",`+
				`"url":"%s/download/app.apk","digest":"sha256:%x"}]}`, server.URL, sum)
		case "/download/app.apk":
			_, _ = io.WriteString(w, appAsset)
		default:
			http.NotFound(w, r)
		}
	}
	server = httptest.NewServer(http.HandlerFunc(handler))
	t.Cleanup(server.Close)
	out := filepath.Join(t.TempDir(), "outputs")
	t.Setenv("GITHUB_API_URL", server.URL)
	t.Setenv("GITHUB_OUTPUT", out)
	t.Setenv("RELEASES_TOKEN", "")
	t.Setenv("GRAVES_SOURCE", "app")
	t.Setenv("GRAVES_TARGET", "android")
	t.Setenv("GRAVES_MODE", "draft")
	if err := execute(context.Background(), root, "fetch"); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256([]byte(appAsset))
	if got := readOutputs(t, out)["sha"]; got != hex.EncodeToString(sum[:]) {
		t.Fatal(got)
	}
	data, err := os.ReadFile(filepath.Join(root, ".graves", "upstream", "app.apk"))
	if err != nil || string(data) != appAsset {
		t.Fatalf("%q %v", data, err)
	}
}
func Test_run(t *testing.T) {
	for _, args := range [][]string{nil, {"unknown"}, {"prepare", "extra"}} {
		if got := run(context.Background(), args); got != 2 {
			t.Fatal(got)
		}
	}
	for _, args := range [][]string{{"help"}, {"-h"}, {"--help"}} {
		if got := run(context.Background(), args); got != 0 {
			t.Fatal(got)
		}
	}
	t.Setenv("GITHUB_EVENT_NAME", "schedule")
	if got := run(context.Background(), []string{"prepare"}); got != 1 {
		t.Fatal(got)
	}
}
func TestWorkflowContract(t *testing.T) {
	load := func(name string) map[string]any {
		t.Helper()
		file := filepath.Join("..", "..", "..", ".github", "workflows", name)
		data, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		var workflow map[string]any
		if err := yaml.Unmarshal(data, &workflow); err != nil {
			t.Fatal(err)
		}
		return workflow
	}
	build := load("build.yaml")
	events := build["on"].(map[string]any)
	if _, exists := events["schedule"]; exists {
		t.Fatal("scheduled application build")
	}
	if _, exists := events["push"].(map[string]any)["branches"]; exists {
		t.Fatal("branch application build")
	}
	jobs := build["jobs"].(map[string]any)
	for _, jobName := range []string{"prepare", "build"} {
		job := jobs[jobName].(map[string]any)
		for _, raw := range job["steps"].([]any) {
			step := raw.(map[string]any)
			uses, _ := step["uses"].(string)
			if !strings.HasPrefix(uses, "actions/upload-artifact@") {
				continue
			}
			settings := step["with"].(map[string]any)
			if settings["include-hidden-files"] != true ||
				settings["if-no-files-found"] != "error" {
				t.Fatal("hidden .graves artifacts would be silently omitted")
			}
		}
	}
	tests := load("repo-test.yaml")["jobs"].(map[string]any)
	if _, exists := tests["all-green"]; exists {
		t.Fatal("obsolete all-green job")
	}
	for _, name := range []string{"tests", "linters", "MegaLinter"} {
		job := tests[name].(map[string]any)
		guard := job["if"].(string)
		if !strings.Contains(guard, "dependabot[bot]") ||
			!strings.Contains(guard, "head.repo.full_name == github.repository") {
			t.Fatal("untrusted PR guard missing")
		}
	}
	for _, workflowJobs := range []map[string]any{jobs, tests} {
		for name, rawJob := range workflowJobs {
			job := rawJob.(map[string]any)
			for _, raw := range job["steps"].([]any) {
				step := raw.(map[string]any)
				settings, ok := step["with"].(map[string]any)
				if ok && settings["repository"] == "megalomania428/go-lib-ci" {
					t.Fatalf("%s checks out go-lib-ci instead of using the Go module", name)
				}
			}
		}
	}
	for jobName, rawJob := range jobs {
		job := rawJob.(map[string]any)
		if env, ok := job["env"].(map[string]any); ok {
			if _, exists := env["RELEASES_TOKEN"]; exists {
				t.Fatalf("job %s exposes RELEASES_TOKEN to all its steps", jobName)
			}
		}
		for _, raw := range job["steps"].([]any) {
			step := raw.(map[string]any)
			name, _ := step["name"].(string)
			env, _ := step["env"].(map[string]any)
			_, exposed := env["RELEASES_TOKEN"]
			allowed := name == "prepare matrix, releases and notes" ||
				name == "fetch upstream and downloads" || name == "upload assets"
			if exposed != allowed {
				t.Fatalf("%s/%s: RELEASES_TOKEN exposed %t, expected %t",
					jobName, name, exposed, allowed)
			}
		}
	}
	buildJob := jobs["build"].(map[string]any)
	var packageStep map[string]any
	for _, raw := range buildJob["steps"].([]any) {
		if step := raw.(map[string]any); step["name"] == "build and package" {
			packageStep = step
		}
	}
	env, _ := packageStep["env"].(map[string]any)
	key, _ := env["APK_SIGNING_KEY"].(string)
	cert, _ := env["APK_SIGNING_CERT"].(string)
	if len(env) != 2 ||
		!strings.Contains(key, "contains(matrix.secrets, 'APK_SIGNING_KEY')") ||
		!strings.Contains(cert, "contains(matrix.vars, 'APK_SIGNING_CERT')") {
		t.Fatalf("build and package env: %v", env)
	}
	jobEnv, _ := buildJob["env"].(map[string]any)
	if jobEnv["GRAVES_RELEASE_ID"] != "${{ matrix.release_id }}" ||
		jobEnv["GRAVES_RELEASE_REPO"] != "${{ matrix.release_repo }}" {
		t.Fatalf("release entry is not taken from the matrix: %v", jobEnv)
	}
	text, err := os.ReadFile(
		filepath.Join("..", "..", "..", ".github", "workflows", "build.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	for _, banned := range []string{
		"toJSON(secrets)", "secrets[", "rev-parse", "outputs.release_id",
	} {
		if strings.Contains(string(text), banned) {
			t.Fatalf("workflow contains %s", banned)
		}
	}
}
func Test_guardPR(t *testing.T) {
	file := filepath.Join(t.TempDir(), "event.json")
	t.Setenv("GITHUB_EVENT_PATH", file)
	t.Setenv("GITHUB_REPOSITORY", "owner/repo")
	for _, tt := range []struct {
		repo, user string
		fail       bool
	}{{
		"owner/repo",
		"human",
		false,
	}, {
		"fork/repo",
		"human",
		true,
	}, {
		"owner/repo",
		"dependabot[bot]",
		true,
	}} {
		data, _ := json.Marshal(
			map[string]any{
				"pull_request": map[string]any{
					"head": map[string]any{"repo": map[string]any{"full_name": tt.repo}},
					"user": map[string]string{"login": tt.user},
				},
			},
		)
		if err := os.WriteFile(file, data, 0o644); err != nil {
			t.Fatal(err)
		}
		if err := guardPR(); (err != nil) != tt.fail {
			t.Fatalf("%+v %v", tt, err)
		}
	}
	if err := os.WriteFile(file, []byte("broken"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := guardPR(); err == nil {
		t.Fatal("invalid event")
	}
}
