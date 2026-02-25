$r = Invoke-RestMethod -Method POST -Uri 'http://localhost:3456/api/v1/auth/login' `
  -ContentType 'application/json' `
  -Body '{"email":"admin@picogallery.local","password":"admin"}' `
  -ErrorAction Stop

$token = $r.access_token
Write-Output "Login OK. Token starts: $($token.Substring(0,30))..."

$h = @{ Authorization = "Bearer $token" }

$assets = Invoke-RestMethod -Method GET -Uri 'http://localhost:3456/api/v1/assets?page_size=20' -Headers $h
Write-Output "Total assets in DB: $($assets.total)"
Write-Output "Assets returned: $($assets.assets.Count)"

if ($assets.assets.Count -gt 0) {
    $assets.assets | Select-Object id, filename, media_type, taken_at | Format-Table
}
