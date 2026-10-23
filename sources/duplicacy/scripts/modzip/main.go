// cspell:ignore modzip GOPROXY

// Command modzip writes one module version as a file-based GOPROXY tree.
package main

import (
	"encoding/json"
	"os"
	"path/filepath"

	"golang.org/x/mod/module"
	"golang.org/x/mod/zip"
)

func main() {
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

func check(err error) {
	if err != nil {
		panic(err)
	}
}
