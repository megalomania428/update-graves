// cspell:ignore modzip GOPROXY dirhash sumdb

// Command modzip writes one module version as a file-based GOPROXY tree, or with
// "extra" copies prebuilt module versions checked against their go.sum hashes.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/mod/module"
	"golang.org/x/mod/semver"
	"golang.org/x/mod/sumdb/dirhash"
	"golang.org/x/mod/zip"
)

func main() {
	if len(os.Args) == 4 && os.Args[1] == "extra" {
		extra(os.Args[2], os.Args[3])
		return
	}
	path, version, src := os.Args[1], os.Args[2], os.Args[3]
	out, stamp := os.Args[4], os.Args[5]
	check(module.Check(path, version))
	escaped, err := module.EscapePath(path)
	check(err)
	dir := filepath.Join(out, escaped, "@v")
	check(os.MkdirAll(dir, 0o755))
	file, err := os.Create(filepath.Join(dir, version+".zip"))
	check(err)
	check(zip.CreateFromDir(file, module.Version{Path: path, Version: version}, src))
	check(file.Close())
	mod, err := os.ReadFile(filepath.Join(src, "go.mod"))
	check(err)
	check(os.WriteFile(filepath.Join(dir, version+".mod"), mod, 0o644))
	info, err := json.Marshal(map[string]string{"Version": version, "Time": stamp})
	check(err)
	check(os.WriteFile(filepath.Join(dir, version+".info"), info, 0o644))
	check(os.WriteFile(filepath.Join(dir, "list"), []byte(version+"\n"), 0o644))
}

// sums holds the go.sum hashes of the zip and the go.mod of one module version.
type sums struct{ zip, mod string }

// extra copies the .info, .mod and .zip of every module version of src/go.sum
// from the GOPROXY tree src to out, writes list files and fails unless the
// hashes of the copies match go.sum and src holds no other files.
func extra(src, out string) {
	hashes := readSums(filepath.Join(src, "go.sum"))
	known := map[string]bool{"go.sum": true}
	lists := map[string][]string{}
	for v, h := range hashes {
		if h.zip == "" || h.mod == "" {
			check(fmt.Errorf("go.sum: %s %s needs both hashes", v.Path, v.Version))
		}
		escPath, err := module.EscapePath(v.Path)
		check(err)
		escVersion, err := module.EscapeVersion(v.Version)
		check(err)
		check(os.MkdirAll(filepath.Join(out, escPath, "@v"), 0o755))
		rel := filepath.Join(escPath, "@v", escVersion)
		for _, ext := range []string{".info", ".mod", ".zip"} {
			known[filepath.ToSlash(rel+ext)] = true
			data, err := os.ReadFile(filepath.Join(src, rel+ext))
			check(err)
			check(writeNew(filepath.Join(out, rel+ext), data))
		}
		verify(v, filepath.Join(out, rel), h)
		lists[escPath] = append(lists[escPath], v.Version)
	}
	for escPath, versions := range lists {
		semver.Sort(versions)
		list := []byte(strings.Join(versions, "\n") + "\n")
		check(writeNew(filepath.Join(out, escPath, "@v", "list"), list))
	}
	check(filepath.WalkDir(src, func(name string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return err
		}
		rel, err := filepath.Rel(src, name)
		if err != nil {
			return err
		}
		if !known[filepath.ToSlash(rel)] {
			return fmt.Errorf("%s: no module version of go.sum owns it", rel)
		}
		return nil
	}))
}

// readSums parses a go.sum file into the zip and go.mod hashes of each version.
func readSums(name string) map[module.Version]*sums {
	data, err := os.ReadFile(name)
	check(err)
	hashes := map[module.Version]*sums{}
	for _, line := range strings.Split(strings.TrimRight(string(data), "\r\n"), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 3 || !strings.HasPrefix(fields[2], "h1:") {
			check(fmt.Errorf("go.sum: malformed line %q", line))
		}
		v := module.Version{Path: fields[0]}
		v.Version = strings.TrimSuffix(fields[1], "/go.mod")
		check(module.Check(v.Path, v.Version))
		if hashes[v] == nil {
			hashes[v] = &sums{}
		}
		hash := &hashes[v].zip
		if v.Version != fields[1] {
			hash = &hashes[v].mod
		}
		if *hash != "" {
			check(fmt.Errorf("go.sum: duplicate line %q", line))
		}
		*hash = fields[2]
	}
	return hashes
}

// verify fails unless the zip and the go.mod at base match the go.sum hashes h
// and the .info at base names the version of v.
func verify(v module.Version, base string, h *sums) {
	sum, err := dirhash.HashZip(base+".zip", dirhash.Hash1)
	if err != nil {
		check(fmt.Errorf("%s %s: zip hash: %w", v.Path, v.Version, err))
	}
	if sum != h.zip {
		check(fmt.Errorf("%s %s: zip hash %s, go.sum %s", v.Path, v.Version, sum, h.zip))
	}
	sum, err = dirhash.Hash1([]string{"go.mod"}, func(string) (io.ReadCloser, error) {
		return os.Open(base + ".mod")
	})
	check(err)
	if sum != h.mod {
		check(fmt.Errorf("%s %s: go.mod hash %s, go.sum %s", v.Path, v.Version, sum,
			h.mod))
	}
	data, err := os.ReadFile(base + ".info")
	check(err)
	var info struct{ Version string }
	check(json.Unmarshal(data, &info))
	if info.Version != v.Version {
		check(fmt.Errorf("%s %s: .info has version %q", v.Path, v.Version, info.Version))
	}
}

// writeNew writes data to the file name, which must not exist yet.
func writeNew(name string, data []byte) error {
	file, err := os.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		return err
	}
	_, err = file.Write(data)
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	return err
}

func check(err error) {
	if err != nil {
		panic(err)
	}
}
