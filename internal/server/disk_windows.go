//go:build windows

package server

import "golang.org/x/sys/windows"

func diskUsage(path string) (*diskStat, error) {
	var freeBytes, totalBytes, totalFreeBytes uint64
	pathPtr, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return nil, err
	}
	err = windows.GetDiskFreeSpaceEx(pathPtr, &freeBytes, &totalBytes, &totalFreeBytes)
	if err != nil {
		return nil, err
	}
	return &diskStat{
		Total: totalBytes,
		Free:  freeBytes,
	}, nil
}
