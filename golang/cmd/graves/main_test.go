package main

import (
	"context"
	"encoding/json"
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
	return root
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
	t.Setenv("GRAVES_SOURCES", "pi-web opencode")
	t.Setenv("GRAVES_DRY_RUN", "1")
	if err := prepare(context.Background(), root, true); err != nil {
		t.Fatal(err)
	}
	values := readOutputs(t, out)
	var matrix selector.Matrix
	if err := json.Unmarshal([]byte(values["matrix"]), &matrix); err != nil {
		t.Fatal(err)
	}
	if values["mode"] != "draft" || values["has_jobs"] != "true" ||
		values["telegram"] != "false" ||
		len(matrix.Include) != 3 ||
		matrix.Include[0].CacheKey == "" ||
		matrix.Include[2].CacheKey != "" {
		t.Fatal(values)
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
