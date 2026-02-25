//go:build linux || darwin

package server

import "syscall"

func diskUsage(path string) (*diskStat, error) {
	var s syscall.Statfs_t
	if err := syscall.Statfs(path, &s); err != nil {
		return nil, err
	}
	return &diskStat{
		Total: s.Blocks * uint64(s.Bsize),
		Free:  s.Bfree * uint64(s.Bsize),
	}, nil
}
