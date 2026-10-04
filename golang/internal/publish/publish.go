// Package publish coordinates destination-specific GitHub release operations.
package publish

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/manifest"
)

var newGitHubClient = ci.NewGitHubClient

// Publisher holds the publication settings of one workflow invocation.
type Publisher struct {
	Root, Repo, Mode, Tag, Name string
	Dry                         bool
	Manifest                    *manifest.Manifest
}

// Destination binds a repository to the token allowed to write it.
type Destination struct{ Repo, Token string }

// Destinations validates GitHub targets; drafts always stay in the current repo.
func (p *Publisher) Destinations() ([]Destination, error) {
	parts := strings.Split(p.Repo, "/")
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return nil, fmt.Errorf("GITHUB_REPOSITORY must be owner/name")
	}
	if p.Mode == "draft" {
		return []Destination{{p.Repo, os.Getenv("GH_TOKEN")}}, nil
	}
	var dest []Destination
	seen := map[string]bool{}
	for _, target := range p.Manifest.Release.Targets {
		d := Destination{}
		switch target.Type {
		case "self":
			d = Destination{p.Repo, os.Getenv("GH_TOKEN")}
		case "repo":
			d = Destination{parts[0] + "/" + target.Repo, os.Getenv("RELEASES_TOKEN")}
		default:
			continue
		}
		if !seen[d.Repo] {
			dest = append(dest, d)
			seen[d.Repo] = true
		}
	}
	for _, d := range dest {
		if !p.Dry && d.Token == "" {
			return nil, fmt.Errorf("release token required for %s", d.Repo)
		}
	}
	return dest, nil
}
func client(d Destination) (*ci.GitHubClient, error) {
	return newGitHubClient(ci.WithGitHubRepo(d.Repo), ci.WithGitHubToken(d.Token))
}

// Ensure creates destination releases before any matrix builds start.
func (p *Publisher) Ensure(ctx context.Context, body string) (int64, error) {
	destinations, err := p.Destinations()
	if err != nil {
		return 0, err
	}
	tag, name := p.Tag, p.Tag
	if p.Mode == "draft" {
		branch := ci.EnvDefault("GITHUB_HEAD_REF", os.Getenv("GITHUB_REF_NAME"))
		tag, name = "v999", "branch ["+branch+"] binaries"
		body = "branch [" + branch + "] build from recent commit"
	}
	var id int64
	for _, d := range destinations {
		if p.Dry {
			fmt.Printf("DRY-RUN ensure release %s %s draft=%t\n",
				d.Repo, tag, p.Mode == "draft")
			continue
		}
		c, err := client(d)
		if err != nil {
			return 0, err
		}
		rel, err := c.EnsureRelease(ctx, ci.WithReleaseTag(tag),
			ci.WithReleaseDraft(p.Mode == "draft"), ci.WithReleaseName(name),
			ci.WithReleaseBody(body))
		if err != nil {
			return 0, err
		}
		if d.Repo == p.Repo {
			id = rel.ID
		}
	}
	return id, nil
}

// Files returns sorted regular built assets and excludes directories and symlinks.
func Files(root string) ([]string, error) {
	dir := filepath.Join(root, ".graves", "out")
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var files []string
	for _, entry := range entries {
		if !entry.Type().IsRegular() {
			return nil, fmt.Errorf("non-file asset: %s", entry.Name())
		}
		files = append(files, filepath.Join(dir, entry.Name()))
	}
	return files, nil
}

// Upload replaces only names produced by this successful matrix job.
func (p *Publisher) Upload(ctx context.Context) error {
	files, err := Files(p.Root)
	if err != nil {
		return err
	}
	if len(files) == 0 {
		return fmt.Errorf("no built assets to upload")
	}
	destinations, err := p.Destinations()
	if err != nil {
		return err
	}
	var draftID int64
	if p.Mode == "draft" {
		draftID, err = strconv.ParseInt(os.Getenv("GRAVES_RELEASE_ID"), 10, 64)
		if err != nil || draftID <= 0 {
			return fmt.Errorf("GRAVES_RELEASE_ID must be a positive integer")
		}
	}
	for _, d := range destinations {
		if p.Dry {
			for _, file := range files {
				fmt.Printf("DRY-RUN upload %s to %s %s\n", filepath.Base(file), d.Repo, p.Tag)
			}
			continue
		}
		c, err := client(d)
		if err != nil {
			return err
		}
		id := draftID
		if p.Mode == "release" {
			rel, err := c.FindRelease(ctx, ci.WithReleaseTag(p.Tag))
			if err != nil {
				return err
			}
			if rel == nil {
				return fmt.Errorf("release %s not found in %s", p.Tag, d.Repo)
			}
			id = rel.ID
		}
		for _, file := range files {
			if _, err := c.UploadAsset(ctx, ci.WithAssetRelease(id), ci.WithAssetPath(file),
				ci.WithAssetReplace(true)); err != nil {
				return err
			}
		}
	}
	return nil
}
