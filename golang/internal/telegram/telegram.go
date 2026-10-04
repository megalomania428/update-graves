// Package telegram publishes the successful targets of a tagged release as one post.
package telegram

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/builder"
	"github.com/megalomania428/update-graves/golang/internal/manifest"
	"github.com/megalomania428/update-graves/golang/internal/notes"
	"github.com/megalomania428/update-graves/golang/internal/publish"
)

var newTelegramClient = ci.NewTelegramClient

// FilterDocuments applies the per-file size limit and Rich Message's media limit.
func FilterDocuments(files []string) ([]string, []string, error) {
	var keep, skipped []string
	for _, file := range files {
		info, err := os.Stat(file)
		if err != nil {
			return nil, nil, err
		}
		if info.Size() > 50*1024*1024 || len(keep) == 50 {
			skipped = append(skipped, filepath.Base(file))
		} else {
			keep = append(keep, file)
		}
	}
	return keep, skipped, nil
}

// Post validates the assembled release and sends the same documents on fallback.
func Post(ctx context.Context, root, name string,
	m *manifest.Manifest, dry bool) error {
	files, err := publish.Files(root)
	if err != nil {
		return err
	}
	if len(files) == 0 {
		return fmt.Errorf("no built assets, nothing to post")
	}
	documents, skipped, err := FilterDocuments(files)
	if err != nil {
		return err
	}
	if len(skipped) > 0 {
		fmt.Fprintf(os.Stderr,
			"skipped (over 50 MiB or 50-document limit, add manually): %s\n",
			strings.Join(skipped, ", "))
	}
	text, err := os.ReadFile(filepath.Join(root, ".graves", "notes", "notes.md"))
	if err != nil {
		return err
	}
	fallback, err := os.ReadFile(filepath.Join(root, ".graves", "notes", "fallback.md"))
	if err != nil {
		return err
	}
	version := builder.Version("release", m)
	header := notes.Header(name, version)
	var chats []string
	for _, dest := range m.Release.Targets {
		if dest.Type == "telegram" {
			chats = append(chats, dest.Chat)
		}
	}
	if len(chats) == 0 {
		return fmt.Errorf("no Telegram destination configured")
	}
	if dry {
		for _, chat := range chats {
			fmt.Printf("DRY-RUN Telegram %s: %d documents\n%s%s\n",
				chat, len(documents), header, text)
		}
		return nil
	}
	c, err := newTelegramClient(ci.WithTelegramToken(os.Getenv("TELEGRAM_BOT_TOKEN")))
	if err != nil {
		return err
	}
	for _, chat := range chats {
		err := c.SendRichPost(ctx, ci.WithPostChat(chat),
			ci.WithPostMarkdown(header+string(text)), ci.WithPostDocuments(documents...))
		var api *ci.TelegramAPIError
		if errors.As(err, &api) && api.Code >= 400 && api.Code < 500 && api.Code != 429 {
			fmt.Fprintf(os.Stderr,
				"WARNING: Telegram rejected notes; retrying with fallback: %v\n", err)
			err = c.SendRichPost(ctx, ci.WithPostChat(chat),
				ci.WithPostMarkdown(header+string(fallback)), ci.WithPostDocuments(documents...))
		}
		if err != nil {
			return err
		}
	}
	return nil
}
