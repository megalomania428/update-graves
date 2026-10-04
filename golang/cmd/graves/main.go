// Command graves builds and publishes applications from declarative recipes.
package main

// cspell:ignore toplevel tele

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/builder"
	"github.com/megalomania428/update-graves/golang/internal/manifest"
	"github.com/megalomania428/update-graves/golang/internal/notes"
	"github.com/megalomania428/update-graves/golang/internal/publish"
	"github.com/megalomania428/update-graves/golang/internal/selector"
	"github.com/megalomania428/update-graves/golang/internal/telegram"
)

const usage = "usage: graves <prepare|fetch|build|upload|telegram>\n" +
	"Settings are read from GITHUB_*, GRAVES_*, LLM_* and token environment variables.\n" +
	"GRAVES_DRY_RUN=1 disables external API writes and LLM calls.\n"

func main() {
	os.Exit(start())
}
func start() int {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	return run(ctx, os.Args[1:])
}
func run(ctx context.Context, args []string) int {
	if len(args) == 1 && (args[0] == "-h" || args[0] == "--help" || args[0] == "help") {
		fmt.Print(usage)
		return 0
	}
	if len(args) != 1 {
		fmt.Fprint(os.Stderr, usage)
		return 2
	}
	switch args[0] {
	case "prepare", "fetch", "build", "upload", "telegram":
	default:
		fmt.Fprint(os.Stderr, usage)
		return 2
	}
	root, err := ci.CommandOutput(ctx,
		ci.WithCommand("git", "rev-parse", "--show-toplevel"))
	if err == nil {
		err = execute(ctx, strings.TrimSpace(root), args[0])
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "graves: %v\n", err)
		return 1
	}
	return 0
}
func execute(ctx context.Context, root, command string) error {
	dry := os.Getenv("GRAVES_DRY_RUN") == "1"
	if command == "prepare" {
		return prepare(ctx, root, dry)
	}
	name, tag := os.Getenv("GRAVES_SOURCE"), os.Getenv("GRAVES_TAG")
	if command == "telegram" {
		names, err := selector.Discover(root)
		if err != nil {
			return err
		}
		name, err = selector.FromTag(tag, names)
		if err != nil {
			return err
		}
	}
	if name == "" || filepath.Base(name) != name || name == "." || name == ".." {
		return fmt.Errorf("GRAVES_SOURCE is required and must be a source directory name")
	}
	m, err := manifest.Load(filepath.Join(root, "sources", name, "build.yaml"))
	if err != nil {
		return err
	}
	if command == "telegram" {
		return telegram.Post(ctx, root, name, m, dry)
	}
	mode := os.Getenv("GRAVES_MODE")
	if mode != "draft" && mode != "release" {
		return fmt.Errorf("GRAVES_MODE must be draft or release")
	}
	if mode == "release" && !strings.HasPrefix(tag, name+"-") {
		return fmt.Errorf("GRAVES_TAG must match %s-*", name)
	}
	if command == "upload" {
		p := &publish.Publisher{Root: root, Repo: os.Getenv("GITHUB_REPOSITORY"),
			Mode: mode, Tag: tag, Name: name, Dry: dry, Manifest: m}
		return p.Upload(ctx)
	}
	target, err := m.TargetByName(os.Getenv("GRAVES_TARGET"))
	if err != nil {
		return err
	}
	b := &builder.Builder{Root: root, Name: name, Mode: mode, Tag: tag,
		Manifest: m, Target: target}
	if command == "fetch" {
		return b.Fetch(ctx)
	}
	return b.Build(ctx)
}
func prepare(ctx context.Context, root string, dry bool) error {
	names, err := selector.Discover(root)
	if err != nil {
		return err
	}
	mode, tag := "draft", ""
	var selected []string
	switch os.Getenv("GITHUB_EVENT_NAME") {
	case "push":
		if os.Getenv("GITHUB_REF_TYPE") != "tag" {
			return fmt.Errorf("branch pushes do not build applications")
		}
		mode, tag = "release", os.Getenv("GITHUB_REF_NAME")
		name, err := selector.FromTag(tag, names)
		if err != nil {
			return err
		}
		selected = []string{name}
	case "workflow_dispatch":
		selected, err = selector.FromList(os.Getenv("GRAVES_SOURCES"), names)
	case "pull_request":
		if err := guardPR(); err != nil {
			return err
		}
		base := os.Getenv("GITHUB_BASE_REF")
		if base == "" {
			return fmt.Errorf("GITHUB_BASE_REF is required")
		}
		var files []string
		files, err = ci.GitChangedFiles(ctx,
			ci.WithGitDir(root), ci.WithGitBase("origin/"+base),
			ci.WithGitHead("HEAD"), ci.WithGitMergeBase(true))
		if err == nil {
			selected, err = selector.FromChanges(root, names, files)
		}
	default:
		return fmt.Errorf("unsupported build event %q", os.Getenv("GITHUB_EVENT_NAME"))
	}
	if err != nil {
		return err
	}
	matrix, err := selector.BuildMatrix(root, selected)
	if err != nil {
		return err
	}
	var id int64
	tele := false
	if len(matrix.Include) != 0 {
		p := &publish.Publisher{Root: root, Repo: os.Getenv("GITHUB_REPOSITORY"),
			Mode: mode, Tag: tag, Dry: dry}
		body := ""
		if mode == "release" {
			p.Name = selected[0]
			file := filepath.Join(root, "sources", p.Name, "build.yaml")
			p.Manifest, err = manifest.Load(file)
			if err != nil {
				return err
			}
			// Validate destination credentials before invoking the LLM.
			if _, err := p.Destinations(); err != nil {
				return err
			}
			g := &notes.Generator{Root: root, Name: p.Name, Tag: tag, Manifest: p.Manifest}
			body, err = g.Generate(ctx, dry)
			if err != nil {
				return err
			}
			for _, dest := range p.Manifest.Release.Targets {
				if dest.Type == "telegram" {
					tele = true
				}
			}
		}
		id, err = p.Ensure(ctx, body)
		if err != nil {
			return err
		}
	}
	data, err := json.Marshal(matrix)
	if err != nil {
		return err
	}
	return outputs([]string{
		"matrix=" + string(data), "has_jobs=" + strconv.FormatBool(len(matrix.Include) > 0),
		"mode=" + mode, "tag=" + tag, "release_id=" + strconv.FormatInt(id, 10),
		"telegram=" + strconv.FormatBool(tele),
	})
}
func outputs(lines []string) error {
	var writer io.Writer = os.Stdout
	if file := os.Getenv("GITHUB_OUTPUT"); file != "" {
		f, err := os.OpenFile(file, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			return err
		}
		defer func() { _ = f.Close() }()
		writer = f
	}
	_, err := fmt.Fprintln(writer, strings.Join(lines, "\n"))
	return err
}
func guardPR() error {
	file := os.Getenv("GITHUB_EVENT_PATH")
	if file == "" {
		return nil
	}
	data, err := os.ReadFile(file)
	if err != nil {
		return err
	}
	var event struct {
		PullRequest struct {
			Head struct {
				Repo struct {
					FullName string `json:"full_name"`
				} `json:"repo"`
			} `json:"head"`
			User struct {
				Login string `json:"login"`
			} `json:"user"`
		} `json:"pull_request"`
	}
	if err := json.Unmarshal(data, &event); err != nil {
		return err
	}
	if event.PullRequest.Head.Repo.FullName != os.Getenv("GITHUB_REPOSITORY") ||
		event.PullRequest.User.Login == "dependabot[bot]" {
		return fmt.Errorf("fork and dependabot PRs are disabled")
	}
	return nil
}
