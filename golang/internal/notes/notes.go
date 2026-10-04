// Package notes prepares release context and runs an optional release notes LLM.
package notes

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/builder"
	"github.com/megalomania428/update-graves/golang/internal/manifest"
)

//go:embed prompt.md
var prompt string
var commandOutput = ci.CommandOutput
var pause = sleep
var ensurePackages = ci.EnsurePackages

// Generator owns the context of one application's tagged release.
type Generator struct {
	Root, Name, Tag string
	Manifest        *manifest.Manifest
}

// Header is added only to the Telegram post, not the GitHub notes.
func Header(name, version string) string { return "# " + name + " " + version + "\n\n" }

// Budget subtracts the Unicode header length from the configured limit.
func Budget(limit int, name, version string) int {
	return limit - utf8.RuneCountInString(Header(name, version))
}

// PatchFiles returns unique regular patch files in target and application order.
func (g *Generator) PatchFiles() ([]string, error) {
	seen := map[string]bool{}
	var files []string
	for _, target := range g.Manifest.Targets {
		for _, dir := range target.Patches {
			full := filepath.Join(g.Root, "sources", g.Name, dir)
			if seen[full] {
				continue
			}
			seen[full] = true
			entries, err := os.ReadDir(full)
			if err != nil {
				return nil, fmt.Errorf("read patches: %w", err)
			}
			for _, entry := range entries {
				ext := filepath.Ext(entry.Name())
				if entry.Type().IsRegular() && (ext == ".diff" || ext == ".patch") {
					files = append(files, filepath.Join(full, entry.Name()))
				}
			}
		}
	}
	return files, nil
}

// Fallback produces deterministic notes and shortens the patch list to the budget.
func Fallback(name, version string, upstream manifest.Upstream, files []string,
	budget int) string {
	prefix := fmt.Sprintf("%s %s\n\nUpstream: %s %s\n\nPatches:\n",
		name, version, upstream.URL, upstream.Ref)
	suffix := "\nGenerated without LLM release notes."
	patches, seen := []string{}, map[string]bool{}
	for _, file := range files {
		base := filepath.Base(file)
		if !seen[base] {
			patches = append(patches, "- "+base+"\n")
			seen[base] = true
		}
	}
	for keep := len(patches); keep >= 0; keep-- {
		text := prefix + strings.Join(patches[:keep], "")
		if keep < len(patches) {
			text += fmt.Sprintf("- …and %d more\n", len(patches)-keep)
		}
		text += suffix
		if utf8.RuneCountInString(text) <= budget {
			return text
		}
	}
	// Extremely long release labels or URLs cannot fit even without patches.
	if budget <= 0 {
		return ""
	}
	minimal := prefix
	if len(patches) > 0 {
		minimal += fmt.Sprintf("- …and %d more\n", len(patches))
	}
	runes := []rune(minimal + suffix)
	return string(runes[:min(budget-1, len(runes))]) + "…"
}

// Generate writes both notes.md and fallback.md, using no LLM in dry-run mode.
func (g *Generator) Generate(ctx context.Context, dry bool) (string, error) {
	version := builder.Version("release", g.Manifest)
	budget := Budget(g.Manifest.Release.NotesLimit, g.Name, version)
	if budget <= 0 {
		return "", fmt.Errorf("release header exceeds notes_limit")
	}
	files, err := g.PatchFiles()
	if err != nil {
		return "", err
	}
	fallback := Fallback(g.Name, version, g.Manifest.Upstream, files, budget)
	text := fallback
	if !dry {
		missing := os.Getenv("LLM_URL") == "" || os.Getenv("LLM_NAME") == "" ||
			os.Getenv("LLM_KEY") == ""
		if missing {
			warn(fmt.Errorf("missing LLM credentials"))
		} else {
			text = g.generateLLM(ctx, files, fallback, budget)
		}
	}
	dir := filepath.Join(g.Root, ".graves", "notes")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	for name, data := range map[string]string{"notes.md": text, "fallback.md": fallback} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(data), 0o644); err != nil {
			return "", err
		}
	}
	return text, nil
}
func (g *Generator) generateLLM(ctx context.Context, files []string, fallback string,
	budget int) string {
	dir, err := os.MkdirTemp("", "graves-notes-")
	if err == nil {
		err = os.Chmod(dir, 0o755)
	}
	if err != nil {
		warn(err)
		return fallback
	}
	defer func() { _ = os.RemoveAll(dir) }()
	if err := g.makeContext(ctx, dir, files, budget); err != nil {
		warn(err)
		return fallback
	}
	cfg, err := os.MkdirTemp("", "graves-notes-config-")
	if err != nil {
		warn(err)
		return fallback
	}
	defer func() { _ = os.RemoveAll(cfg) }()
	if err := writeConfig(cfg); err != nil {
		warn(err)
		return fallback
	}
	err = ensurePackages(ctx, []string{"podman"}, ci.EnsurePackagesOptions{})
	if err != nil {
		warn(err)
		return fallback
	}
	attached, err := contextFiles(dir)
	if err != nil {
		warn(err)
		return fallback
	}
	args := g.containerArgs(dir, cfg, attached, budget)
	text, err := attemptNotes(ctx, budget, func(ctx context.Context) (string, error) {
		return commandOutput(ctx, ci.WithCommand("podman", args...))
	})
	if err != nil {
		warn(err)
		return fallback
	}
	return text
}
func warn(err error) {
	fmt.Fprintf(os.Stderr, "WARNING: LLM release notes failed: %v\n", err)
}
func attemptNotes(ctx context.Context, budget int,
	run func(context.Context) (string, error)) (string, error) {
	var last error
	delays := []time.Duration{0, 3 * time.Second, 9 * time.Second, 15 * time.Second}
	for i, delay := range delays {
		if i > 0 {
			if err := pause(ctx, delay); err != nil {
				return "", err
			}
		}
		text, err := run(ctx)
		text = strings.TrimSpace(text)
		if err == nil && text != "" && utf8.RuneCountInString(text) <= budget {
			return text, nil
		}
		last = err
		if err == nil {
			last = fmt.Errorf("empty or over-budget answer (%d characters, budget %d)",
				utf8.RuneCountInString(text), budget)
		}
	}
	return "", last
}
func sleep(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
func (g *Generator) makeContext(ctx context.Context, dir string, files []string,
	budget int) error {
	write := func(name, data string) error {
		return os.WriteFile(filepath.Join(dir, name), []byte(data), 0o644)
	}
	source := filepath.Join(g.Root, "sources", g.Name)
	build, err := os.ReadFile(filepath.Join(source, "build.yaml"))
	if err != nil {
		return err
	}
	readme, err := os.ReadFile(filepath.Join(source, "README.md"))
	if err != nil {
		return err
	}
	app := fmt.Sprintf("Application: %s\nTag: %s\nUpstream: %s %s\n\n%s\n\n%s",
		g.Name, g.Tag, g.Manifest.Upstream.URL, g.Manifest.Upstream.Ref, build, readme)
	previous, err := ci.GitPreviousTag(ctx, ci.WithGitDir(g.Root), ci.WithGitRef(g.Tag),
		ci.WithGitMatch(g.Name+"-*"))
	if err != nil {
		return err
	}
	if previous == "" {
		app += "\nThis is the first release of this application.\n"
	}
	if err := write("app.md", app); err != nil {
		return err
	}
	var list []string
	for i, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			return err
		}
		owner := "shared"
		for _, target := range g.Manifest.Targets {
			for _, patchDir := range target.Patches {
				if filepath.Dir(file) == filepath.Join(source, patchDir) {
					owner = target.Name
					break
				}
			}
			if owner != "shared" {
				break
			}
		}
		rel := filepath.Join("patches", owner, filepath.Base(file))
		dest := filepath.Join(dir, rel)
		if _, err := os.Stat(dest); err == nil {
			return fmt.Errorf("duplicate context patch path %s", rel)
		}
		if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(dest, data, 0o644); err != nil {
			return err
		}
		list = append(list, fmt.Sprintf("%d. %s", i+1, filepath.ToSlash(rel)))
	}
	if err := write("patches.md", strings.Join(list, "\n")); err != nil {
		return err
	}
	system := strings.ReplaceAll(prompt, "<budget>", strconv.Itoa(budget))
	if err := write("prompt.md", system); err != nil {
		return err
	}
	if previous == "" {
		return nil
	}
	paths := append([]string{"sources/" + g.Name}, g.Manifest.Shared...)
	diff, err := ci.GitDiff(ctx, ci.WithGitDir(g.Root), ci.WithGitBase(previous),
		ci.WithGitHead(g.Tag), ci.WithGitPaths(paths...))
	if err != nil {
		return err
	}
	if len(diff) > 300*1024 {
		diff = diff[:300*1024]
		for !utf8.ValidString(diff) {
			diff = diff[:len(diff)-1]
		}
		diff += "\n[truncated]\n"
	}
	if err := write("changes.diff", diff); err != nil {
		return err
	}
	log, err := ci.GitLog(ctx, ci.WithGitDir(g.Root), ci.WithGitBase(previous),
		ci.WithGitHead(g.Tag), ci.WithGitPaths(paths...))
	if err != nil {
		return err
	}
	if err := write("changes.log", log); err != nil {
		return err
	}
	if err := g.upstreamLog(ctx, previous, dir); err != nil {
		fmt.Fprintf(os.Stderr, "WARNING: upstream release context unavailable: %v\n", err)
	}
	return nil
}
func (g *Generator) upstreamLog(ctx context.Context, previous, dir string) error {
	old, err := ci.GitShow(ctx, ci.WithGitDir(g.Root), ci.WithGitRef(previous),
		ci.WithGitPaths("sources/"+g.Name+"/build.yaml"))
	if err != nil {
		return err
	}
	m, err := manifest.Decode([]byte(old), previous+":build.yaml")
	if err != nil {
		return err
	}
	current := g.Manifest.Upstream
	if m.Upstream.URL != current.URL || m.Upstream.Ref == current.Ref {
		return nil
	}
	clone, err := os.MkdirTemp("", "graves-upstream-log-")
	if err != nil {
		return err
	}
	defer func() { _ = os.RemoveAll(clone) }()
	err = ci.GitClone(ctx, ci.WithGitURL(m.Upstream.URL), ci.WithGitRef(current.Ref),
		ci.WithGitDir(clone), ci.WithGitBare(true), ci.WithGitFilter("blob:none"))
	if err != nil {
		return err
	}
	log, err := ci.GitLog(ctx, ci.WithGitDir(clone), ci.WithGitBase(m.Upstream.Ref),
		ci.WithGitHead(g.Manifest.Upstream.Ref), ci.WithGitMaxCount(300))
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "upstream.log"), []byte(log), 0o644)
}
func writeConfig(dir string) error {
	boolEnv := func(key string) (bool, error) {
		return strconv.ParseBool(ci.EnvDefault(key, "true"))
	}
	eff, err := boolEnv("LLM_EFF")
	if err != nil {
		return fmt.Errorf("LLM_EFF: %w", err)
	}
	reasoning, err := boolEnv("LLM_RSN")
	if err != nil {
		return fmt.Errorf("LLM_RSN: %w", err)
	}
	window, err := ci.ParseIntEnv("LLM_CTX", 1000000)
	if err != nil {
		return err
	}
	maxTokens, err := ci.ParseIntEnv("LLM_MAX", 131072)
	if err != nil {
		return err
	}
	if window <= 0 || maxTokens <= 0 {
		return fmt.Errorf("LLM_CTX and LLM_MAX must be positive")
	}
	name := os.Getenv("LLM_NAME")
	settings := map[string]any{
		"packages": []string{"npm:pi-mcp-adapter"}, "quietStartup": true,
		"enableAnalytics": false, "collapseChangelog": true, "enableInstallTelemetry": false,
		"lastChangelogVersion": "0.85.1", "defaultProvider": "omni", "defaultModel": name,
		"defaultThinkingLevel": ci.EnvDefault("LLM_LVL", "max"), "theme": "dark"}
	model := map[string]any{"id": name, "reasoning": reasoning, "contextWindow": window,
		"maxTokens": maxTokens,
		"thinkingLevelMap": map[string]string{"low": "low", "medium": "medium",
			"high": "high", "xhigh": "xhigh", "max": "max"}}
	provider := map[string]any{"baseUrl": os.Getenv("LLM_URL"),
		"api": ci.EnvDefault("LLM_API", "openai-responses"), "apiKey": os.Getenv("LLM_KEY"),
		"compat": map[string]bool{"supportsReasoningEffort": eff},
		"models": []any{model}}
	servers := map[string]any{}
	prefixes := map[string]string{"wripy": "FETCH_MCP", "snomcy": "SEARCH_MCP"}
	for name, prefix := range prefixes {
		u, key := os.Getenv(prefix+"_URL"), os.Getenv(prefix+"_KEY")
		if u != "" && key != "" {
			servers[name] = map[string]any{"url": u,
				"headers":     map[string]string{"Authorization": "Bearer " + key},
				"directTools": false}
		}
	}
	for file, value := range map[string]any{"settings.json": settings,
		"models.json": map[string]any{"providers": map[string]any{"omni": provider}},
		"mcp.json":    map[string]any{"mcpServers": servers}} {
		data, err := json.Marshal(value)
		if err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(dir, file), data, 0o644); err != nil {
			return err
		}
	}
	return nil
}
func contextFiles(dir string) ([]string, error) {
	var files []string
	err := filepath.WalkDir(dir, func(file string, entry fs.DirEntry, err error) error {
		if err != nil || !entry.Type().IsRegular() {
			return err
		}
		rel, err := filepath.Rel(dir, file)
		if err == nil && rel != "prompt.md" {
			files = append(files, filepath.ToSlash(rel))
		}
		return err
	})
	return files, err
}
func (g *Generator) containerArgs(dir, cfg string, files []string, budget int) []string {
	args := []string{"run", "--rm", "--network=host", "-e", "PI_MCP_CONFIG_MODE=exclusive"}
	for _, env := range builder.ProxyEnvironment() {
		args = append(args, "-e", env)
	}
	for _, name := range []string{"settings", "models", "mcp"} {
		mount := filepath.Join(
			cfg,
			name+".json",
		) + ":/home/coder/.pi/agent/" + name + ".json:ro"
		args = append(args, "-v", mount)
	}
	version := builder.Version("release", g.Manifest)
	message := fmt.Sprintf("Write the release notes for %s %s "+
		"from the attached files. "+
		"The whole answer must not exceed %d characters.", g.Name, version, budget)
	args = append(args, "-v", dir+":/workspace/notes:ro", "-w", "/workspace/notes",
		"--name",
		"graves-notes-"+ci.EnvDefault("GITHUB_RUN_ID", "local")+"-"+filepath.Base(dir),
		ci.EnvDefault("NOTES_IMAGE", "ghcr.io/raven428/review-pi_dev:latest"), "pi",
		"--mcp-config", "/home/coder/.pi/agent/mcp.json", "--no-session",
		"--no-context-files", "--tools", "mcp",
		"--append-system-prompt", "/workspace/notes/prompt.md", "-p", message)
	// Attached context replaces file tools, which could read the mounted credentials.
	for _, file := range files {
		args = append(args, "@"+file)
	}
	return args
}
