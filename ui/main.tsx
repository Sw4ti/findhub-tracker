import React, { FormEvent, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import './style.css'
import { mapSelectionSummary } from './mapSelectionSummary'

type Point = { device: string; label: string; reported: string; collected: string; latitude: number; longitude: number; accuracy: number | null; status: string }
type DeviceStatus = 'reported' | 'no-report' | 'error' | 'pending' | 'unknown'
type AccountStatus = 'active' | 'unavailable' | 'removed' | 'unknown'
type DiscoveryStatus = 'never' | 'running' | 'success' | 'authentication_error' | 'rate_limited' | 'unavailable' | 'error'
type History = { points: Point[]; devices: { device: string; label: string; total: number; latest: string | null; attempted: string | null; status: DeviceStatus; account_status: AccountStatus; deletion_allowed: boolean; deletion_reason: string; deletion_history_count: number }[]; timezone: string; demo: boolean; discovery_enabled: boolean; state: { succeeded: string; error: string | null } | null; discovery: { status: DiscoveryStatus; attempted: string | null; succeeded: string | null; error: string | null } | null }
const statusLabels: Record<DeviceStatus, string> = {
  reported: 'Report received',
  'no-report': 'No report available',
  error: 'Collection error',
  pending: 'Awaiting first collection',
  unknown: 'Collection status unavailable',
}
const accountStatusLabels: Record<AccountStatus, string> = {
  active: 'Google account: active', unavailable: 'Google account: temporarily unavailable',
  removed: 'Google account: removed (history retained)', unknown: 'Google account: not checked',
}
const discoveryLabels: Record<DiscoveryStatus, string> = {
  never: 'Waiting for first device discovery', running: 'Refreshing Google device list…',
  success: 'Device list refreshed', authentication_error: 'Google or collector authentication needs attention',
  rate_limited: 'Google rate limit reached; automatic retry is scheduled',
  unavailable: 'Google device list temporarily unavailable; existing devices are retained',
  error: 'Device discovery failed; existing devices are retained',
}
const colors = ['#75e4bc', '#f2bc77', '#aebdfd', '#eb9cbd']

function MapView({ points, active, focusedDevice, select, format, devices }: { points: Point[]; active: number | null; focusedDevice: string; select: (i: number) => void; format: (s: string) => string; devices: string[] }) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map>(null)
  const layer = useRef<L.LayerGroup>(null)
  const markers = useRef<L.Marker[]>([])
  const popupContent = useRef<HTMLElement[]>([])
  const [tileError, setTileError] = useState(false)
  const showTileError = () => setTileError(true)
  useEffect(() => {
    const instance = L.map(container.current!, { scrollWheelZoom: false, touchZoom: true, dragging: true }).setView([12.345, 45.678], 13)
    map.current = instance
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).on('tileerror', showTileError).addTo(instance)
    layer.current = L.layerGroup().addTo(instance)
    const resizeMap = () => requestAnimationFrame(() => instance.invalidateSize({ pan: false }))
    const resize = new ResizeObserver(resizeMap)
    resize.observe(container.current!)
    window.addEventListener('orientationchange', resizeMap)
    window.visualViewport?.addEventListener('resize', resizeMap)
    return () => { resize.disconnect(); window.removeEventListener('orientationchange', resizeMap); window.visualViewport?.removeEventListener('resize', resizeMap); instance.remove() }
  }, [])
  useEffect(() => {
    const group = layer.current!
    map.current!.closePopup()
    group.clearLayers()
    popupContent.current = []
    const paths = new Map<string, L.LatLngTuple[]>()
    const coords: L.LatLngTuple[] = []
    markers.current = points.map((point, index) => {
      const coordinate: L.LatLngTuple = [point.latitude, point.longitude]
      coords.push(coordinate)
      const route = paths.get(point.device) ?? []
      route.push(coordinate)
      paths.set(point.device, route)
      const color = colors[devices.indexOf(point.device) % colors.length] ?? colors[0]
      const detail = document.createElement('div')
      const title = document.createElement('strong')
      title.textContent = point.label
      detail.append(title, document.createElement('br'), format(point.reported), document.createElement('br'),
        point.accuracy === null ? 'Accuracy unavailable' : 'Accuracy ±' + point.accuracy + ' m')
      const icon = L.divIcon({ className: focusedDevice === point.device ? 'map-pin focused' : 'map-pin', html: '<span style="--pin-color:' + color + '">' + (index + 1) + '</span>', iconSize: [34, 34], iconAnchor: [17, 17] })
      popupContent.current[index] = detail
      return L.marker(coordinate, { icon, title: point.label, alt: 'Detection ' + (index + 1) })
        .addTo(group).on('click', () => select(index))
    })
    paths.forEach((route, device) => L.polyline(route, {
      dashArray: '6 8', weight: 3, color: colors[devices.indexOf(device) % colors.length], interactive: false
    }).addTo(group))
    if (focusedDevice) {
      let latest = -1
      points.forEach((point, index) => {
        if (point.device === focusedDevice && (latest < 0 || point.reported > points[latest].reported)) latest = index
      })
      if (latest >= 0) {
        const marker = markers.current[latest]
        map.current!.setView(marker.getLatLng(), Math.max(map.current!.getZoom(), 15), { animate: true })
      } else if (coords.length) map.current!.fitBounds(L.latLngBounds(coords), { padding: [28, 28], maxZoom: 15 })
    } else if (coords.length) map.current!.fitBounds(L.latLngBounds(coords), { padding: [42, 42], maxZoom: 16 })
    else map.current!.setView([12.345, 45.678], 13)
  }, [points, devices, format, select, focusedDevice, active])
  useEffect(() => {
    if (active !== null && markers.current[active]) {
      const marker = markers.current[active]
      markers.current.forEach(candidate => candidate.closePopup())
      map.current!.closePopup()
      map.current!.panInside(marker.getLatLng(), { padding: [45, 45] })
      L.popup({ maxWidth: 250 }).setLatLng(marker.getLatLng()).setContent(popupContent.current[active]).openOn(map.current!)
    }
  }, [active, points])
  return <div className="map-wrap"><div className="map" ref={container} aria-label="Tracker detection map" />
    {tileError && <div className="map-alert" role="status">Map tiles unavailable. Recorded points are still shown.</div>}</div>
}

function App() {
  const [data, setData] = useState<History | null>(null)
  const [loadedQuery, setLoadedQuery] = useState<string | null>(null)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [historyLoadError, setHistoryLoadError] = useState(false)
  const [signedOut, setSignedOut] = useState(false)
  const [error, setError] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [rememberMe, setRememberMe] = useState(false)
  const [device, setDevice] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [query, setQuery] = useState('')
  const [active, setActive] = useState<number | null>(null)
  const [focusedDevice, setFocusedDevice] = useState('')
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [table, setTable] = useState(false)
  const [reload, setReload] = useState(0)
  const [pending, setPending] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<History['devices'][number] | null>(null)
  const [manageTarget, setManageTarget] = useState<History['devices'][number] | null>(null)
  const [deleteName, setDeleteName] = useState('')
  const dialogRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoadingHistory(true)
    setHistoryLoadError(false)
    fetch('/api/history?' + query, { signal: controller.signal }).then(async response => {
      if (response.status === 401) { setSignedOut(true); setData(null); setLoadingHistory(false); return }
      if (!response.ok) throw new Error('History could not be loaded.')
      setData(await response.json())
      setLoadedQuery(query)
      setLoadingHistory(false)
      setHistoryLoadError(false)
      setError('')
      setSignedOut(false)
      setActive(null)
    }).catch(error => {
      if (error.name !== 'AbortError') {
        setHistoryLoadError(true)
        setLoadingHistory(false)
        setError(error.message)
      }
    })
    return () => controller.abort()
  }, [query, reload])

  useEffect(() => {
    const timer = window.setInterval(() => setReload(value => value + 1), 30000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    const viewport = window.visualViewport
    const updateHeight = () => document.documentElement.style.setProperty('--visual-vh', ((viewport?.height ?? window.innerHeight) * 0.01) + 'px')
    updateHeight()
    viewport?.addEventListener('resize', updateHeight)
    window.addEventListener('resize', updateHeight)
    return () => { viewport?.removeEventListener('resize', updateHeight); window.removeEventListener('resize', updateHeight) }
  }, [])

  useEffect(() => {
    document.body.classList.toggle('modal-open', Boolean(manageTarget || deleteTarget))
    return () => document.body.classList.remove('modal-open')
  }, [manageTarget, deleteTarget])

  useEffect(() => {
    if (!manageTarget && !deleteTarget) return
    const previous = document.activeElement as HTMLElement | null
    const timer = window.setTimeout(() => dialogRef.current?.focus(), 0)
    return () => { window.clearTimeout(timer); if (previous?.isConnected) previous.focus() }
  }, [manageTarget, deleteTarget])

  async function login(event: FormEvent) {
    event.preventDefault()
    setPending(true)
    setError('')
    try {
      const response = await fetch('/api/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password, remember_me: rememberMe })
      })
      if (!response.ok) throw new Error(response.status === 429 ? 'Too many attempts. Try again in a minute.' : 'Sign-in failed.')
      setPassword('')
      setShowPassword(false)
      setRememberMe(false)
      setReload(value => value + 1)
    } catch (error) { setError((error as Error).message) }
    finally { setPending(false) }
  }

  async function action(name: 'logout' | 'refresh' | 'devices/refresh') {
    setError('')
    setPending(true)
    try {
      const response = await fetch('/api/' + name, {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrfToken() }
      })
      if (name === 'logout') {
        if (!response.ok && response.status !== 401) throw new Error('Sign out could not be completed. Try again.')
        setPassword('')
        setRememberMe(false)
        setData(null)
        setSignedOut(true)
        window.history.replaceState(null, '', '/')
        return
      }
      if (!response.ok && response.status === 429) throw new Error('Google rate-limited discovery. The automatic retry will run later.')
      if (!response.ok && (response.status === 503 || response.status === 504)) throw new Error('Discovery is temporarily unavailable. Check its status shortly.')
      if (response.status !== 401 && !response.ok) throw new Error('The request failed.')
      setReload(value => value + 1)
    } catch (error) { setError((error as Error).message) }
    finally { setPending(false) }
  }

  function csrfToken() {
    const pair = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith('tracker_csrf='))
    return pair ? decodeURIComponent(pair.slice('tracker_csrf='.length)) : ''
  }

  async function deleteDevice(event: FormEvent) {
    event.preventDefault()
    if (!deleteTarget || deleteName !== deleteTarget.label) return
    setPending(true)
    setError('')
    try {
      const response = await fetch('/api/devices/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
        body: JSON.stringify({ device_id: deleteTarget.device, confirm_name: deleteName })
      })
      if (!response.ok) {
        const result = await response.json().catch(() => ({}))
        throw new Error(result.detail || 'Device deletion did not complete. The device and its history remain protected until recovery is verified.')
      }
      if (device === deleteTarget.device) setDevice('')
      if (focusedDevice === deleteTarget.device) setFocusedDevice('')
      setQuery('')
      setActive(null)
      setDeleteTarget(null)
      setDeleteName('')
      setReload(value => value + 1)
    } catch (error) { setError((error as Error).message) }
    finally { setPending(false) }
  }

  function filters(event: FormEvent) {
    event.preventDefault()
    setError('')
    try {
      const startIso = start ? sydneyInputToIso(start, data?.timezone ?? 'Australia/Sydney') : ''
      const endIso = end ? sydneyInputToIso(end, data?.timezone ?? 'Australia/Sydney') : ''
      if (startIso && endIso && new Date(startIso) > new Date(endIso)) { setError('Choose an end time after the start time.'); return }
      const params = new URLSearchParams()
      if (device) params.set('device', device)
      if (startIso) params.set('start', startIso)
      if (endIso) params.set('end', endIso)
      setFocusedDevice(device)
      setActive(null)
      setQuery(params.toString())
    } catch (error) { setError((error as Error).message) }
  }

  function focusDevice(id: string) {
    try {
      setFocusedDevice(id)
      setDevice(id)
      setActive(null)
      const params = new URLSearchParams()
      params.set('device', id)
      if (start) params.set('start', sydneyInputToIso(start, data?.timezone ?? 'Australia/Sydney'))
      if (end) params.set('end', sydneyInputToIso(end, data?.timezone ?? 'Australia/Sydney'))
      setQuery(params.toString())
    } catch (error) { setError((error as Error).message) }
  }

  const formatter = React.useMemo(() => new Intl.DateTimeFormat('en-AU', {
    timeZone: data?.timezone ?? 'Australia/Sydney', dateStyle: 'medium', timeStyle: 'short'
  }), [data?.timezone])
  const format = React.useCallback((value: string) => formatter.format(new Date(value)), [formatter])
  const devices = React.useMemo(() => data?.devices.map(row => row.device) ?? [], [data?.devices])
  const mapPoints = data && loadedQuery === query ? data.points : []
  const mapSummary = data ? mapSelectionSummary(query, data.devices, mapPoints.length) : null
  const mapRefreshing = loadingHistory || loadedQuery !== query
  const mapStatus = historyLoadError
    ? loadedQuery === query
      ? 'History refresh failed. Showing the last successful map results.'
      : 'Map results could not be loaded for this selection.'
    : mapRefreshing
      ? 'Updating map results for this selection…'
      : mapPoints.length === 0
        ? 'No detections match this selection.'
        : ''
  const select = React.useCallback((index: number) => {
    setActive(index)
    setFocusedDevice(data?.points[index]?.device ?? '')
  }, [data?.points])

  if (!data) return <main className="welcome"><section className="panel signin">
    <Brand /><h1>{signedOut ? 'Sign in' : 'Loading tracker'}</h1>
    {signedOut && <form onSubmit={login}>
      <label>Tracker password
        <span className="password-field"><input autoComplete="current-password" maxLength={512} type={showPassword ? 'text' : 'password'} value={password} onChange={event => setPassword(event.target.value)} required />
          <button type="button" className="password-toggle" aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? 'Hide' : 'Show'}</button>
        </span>
      </label>
      <label className="remember-choice"><input type="checkbox" checked={rememberMe} onChange={event => setRememberMe(event.target.checked)} />
        <span>Remember this browser for up to 30 days <small>Only select this on a private, screen-locked device. Authelia's separate Remember Me option controls its MFA session.</small></span>
      </label>
      <button className="primary" disabled={pending}>Sign in</button>
    </form>}
    {error && <p role="alert">{error}</p>}
  </section></main>

  return <div className="shell">
    <aside className={'sidebar' + (mobileMenuOpen ? ' menu-open' : '')}>
      <div className="sidebar-head"><Brand /><button className="mobile-menu-toggle" aria-label={mobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'} aria-expanded={mobileMenuOpen} aria-controls="dashboard-navigation" onClick={() => setMobileMenuOpen(value => !value)}><span aria-hidden="true">{mobileMenuOpen ? '×' : '☰'}</span></button></div>
      <div className="sidebar-content" id="dashboard-navigation"><nav aria-label="Dashboard">
        <a className="current" href="#overview" onClick={() => setMobileMenuOpen(false)}>Overview</a><a href="#history" onClick={() => setMobileMenuOpen(false)}>Location history</a>
      </nav><button disabled={pending} className="quiet" onClick={() => { setMobileMenuOpen(false); void action('logout') }}>Sign out</button></div>
    </aside>
    <main id="overview">
      <header className="top"><div><p className="eyebrow">TRACKER DASHBOARD</p><h1>Overview</h1>
        <p className="muted">{data.timezone}{data.demo && ' · Demo data'}</p></div>
        {data.demo
          ? <button disabled={pending} onClick={() => action('refresh')}>Refresh reports</button>
          : <button onClick={() => setReload(value => value + 1)}>Reload history</button>}
      </header>
      {!data.demo && data.discovery_enabled && <section className="panel discovery" aria-label="Device discovery status">
        <div><strong>Device discovery</strong><p className={'device-status ' + (data.discovery?.status ?? 'never')}>{discoveryLabels[data.discovery?.status ?? 'never']}</p>
          <p className="muted">Last successful discovery: {data.discovery?.succeeded ? format(data.discovery.succeeded) : 'Not yet completed'}</p>
          {data.discovery?.error && <p className="muted">{data.discovery.error}</p>}</div>
        <button disabled={pending || data.discovery?.status === 'running'} onClick={() => action('devices/refresh')}>Refresh Devices</button>
      </section>}
      <section className="device-overview" aria-label="Trackers">
        <div className="section-title devices-title"><h2>Your devices</h2><span className="muted">{data.devices.length} {data.devices.length === 1 ? 'tracker' : 'trackers'}</span></div>
        <div className="cards">
          {data.devices.map((item, index) => {
            const latestPoint = data.points.filter(point => point.device === item.device).reduce<Point | null>((latest, point) => !latest || point.reported > latest.reported ? point : latest, null)
            return <article className={'panel tracker' + (focusedDevice === item.device ? ' selected' : '')} key={item.device}>
              <button className="tracker-select" type="button" aria-pressed={focusedDevice === item.device} onClick={() => focusDevice(item.device)}>
                <span className="tracker-card-heading"><span className="device-icon" style={{ '--device-color': colors[index % colors.length] } as React.CSSProperties} aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 21s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12Z"/><circle cx="12" cy="9" r="2.3"/></svg></span>
                  <span className="tracker-heading"><strong className="device-name">{item.label}</strong><span className={'status-pill ' + item.status}><i />{statusLabels[item.status]}</span></span>
                  <span className="card-chevron" aria-hidden="true">›</span>
                </span>
                <span className="tracker-location">{latestPoint ? <><span>{latestPoint.latitude.toFixed(5)}, {latestPoint.longitude.toFixed(5)}</span><small>Last location · {format(latestPoint.reported)}</small></> : <><span>{item.latest ? 'Location outside selected range' : 'No location reported'}</span><small>{item.latest ? 'Adjust the date filter to view this location' : 'Waiting for the first detection'}</small></>}</span>
                <span className="tracker-card-footer"><span>{item.latest ? 'Last seen ' + format(item.latest) : 'No detections yet'}</span><b>{item.total.toLocaleString('en-AU')} <small>reports</small></b></span>
              </button>
              <button type="button" className="manage-trigger" aria-label={'Manage ' + item.label} onClick={() => setManageTarget(item)}><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1a1.7 1.7 0 0 1-2.4 2.4L17 17.4a1.7 1.7 0 0 0-2.9 1.2v.2a1.7 1.7 0 0 1-3.4 0v-.2a1.7 1.7 0 0 0-2.9-1.2l-.1.1a1.7 1.7 0 0 1-2.4-2.4l.1-.1a1.7 1.7 0 0 0-1.2-2.9H4a1.7 1.7 0 0 1 0-3.4h.2a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a1.7 1.7 0 0 1 2.4-2.4l.1.1a1.7 1.7 0 0 0 2.9-1.2V2a1.7 1.7 0 0 1 3.4 0v.2a1.7 1.7 0 0 0 2.9 1.2l.1-.1a1.7 1.7 0 0 1 2.4 2.4l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.2a1.7 1.7 0 0 1 0 3.4h-.2a1.7 1.7 0 0 0-1.2 2.9Z"/></svg><span>Manage</span></button>
            </article>
          })}
          {!data.devices.length && <p className="muted empty-devices">Waiting for the first tracker report.</p>}
        </div>
      </section>
      <form className="panel filters" onSubmit={filters}>
        <div className="device-filter"><label htmlFor="device-filter">Device</label><select id="device-filter" value={device} onChange={event => setDevice(event.target.value)}><option value="">All trackers</option>{data.devices.map(row => <option key={row.device} value={row.device}>{row.label}</option>)}</select></div>
        <label>From (Sydney time)<input type="datetime-local" value={start} onChange={event => setStart(event.target.value)} /></label>
        <label>To (Sydney time)<input type="datetime-local" value={end} onChange={event => setEnd(event.target.value)} /></label>
        <button type="submit" className="primary">Apply filters</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="workspace">
        <section className="panel map-panel"><div className="section-title"><h2>Detection map</h2></div>
          {mapSummary && <div className="map-summary" aria-label="Map selection summary" aria-live="polite">
            <span className="map-summary-chip"><span>Tracker</span><strong title={mapSummary.tracker}>{mapSummary.tracker}</strong></span>
            <span className="map-summary-chip map-time-scope"><span>Date/time range</span><strong>{mapSummary.timeScope}</strong></span>
            <span className="map-summary-chip map-detection-count"><span>Map</span><strong>{mapSummary.count.toLocaleString('en-AU')} detections shown</strong></span>
          </div>}
          {mapStatus && <p className={'map-summary-status' + (historyLoadError ? ' failed' : '')} role="status">{mapStatus}</p>}
          <MapView points={mapPoints} active={active} focusedDevice={focusedDevice} select={select} format={format} devices={devices} />
          <div className="map-legend"><span className="dash" /> Inferred movement between detections</div>
        </section>
        <section className="panel history" id="history">
          <div className="section-title"><h2>Location history</h2></div>
          <div className="tabs"><button aria-pressed={!table} onClick={() => setTable(false)}>Timeline</button><button aria-pressed={table} onClick={() => setTable(true)}>Table</button></div>
          <div className="history-body">
            {data.points.length === 0 && <p className="muted">No detections match these filters.</p>}
            {table ? <div className="history-table-wrap"><table><thead><tr><th>Detection</th><th>Reported</th><th>Accuracy</th></tr></thead><tbody>{data.points.map((point, index) => <tr key={index}><td data-label="Detection"><button onClick={() => select(index)}>{index + 1} · {point.label}</button></td><td data-label="Reported">{format(point.reported)}</td><td data-label="Accuracy">{point.accuracy === null ? '—' : point.accuracy + ' m'}</td></tr>)}</tbody></table></div>
              : <ol>{data.points.map((point, index) => <li key={index}><button className={active === index ? 'entry selected' : 'entry'} onClick={() => select(index)}>
                  <span className="ordinal" style={{ color: colors[devices.indexOf(point.device) % colors.length] }}>{index + 1}</span>
                  <span><strong>{point.label}</strong><span className="muted">{format(point.reported)}{point.accuracy !== null && ' · ±' + point.accuracy + ' m'}</span></span>
                </button></li>)}</ol>}
          </div>
          <div className="exports"><span className="muted">Export</span>{['CSV', 'JSON', 'GPX'].map(type => <a key={type} href={'/api/export/' + type.toLowerCase() + '?' + query}>{type}</a>)}</div>
        </section>
      </div>
      <footer className="muted">{data.state?.succeeded ? 'Last collection ' + format(data.state.succeeded) : 'No successful collections recorded'}</footer>
    </main>
    {manageTarget && !deleteTarget && <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setManageTarget(null) }}>
      <section ref={dialogRef} tabIndex={-1} onKeyDown={event => trapDialogKey(event, () => setManageTarget(null))} className="panel manage-dialog" role="dialog" aria-modal="true" aria-labelledby="manage-title">
        <div className="dialog-heading"><div><p className="eyebrow">DEVICE SETTINGS</p><h2 id="manage-title">{manageTarget.label}</h2></div><button type="button" className="icon-button" aria-label="Close device settings" onClick={() => setManageTarget(null)}>×</button></div>
        <p className={'status-pill ' + manageTarget.status}><i />{statusLabels[manageTarget.status]}</p>
        <dl className="manage-details"><div><dt>Google account status</dt><dd>{accountStatusLabels[manageTarget.account_status ?? 'unknown']}</dd></div><div><dt>Last successful detection</dt><dd>{manageTarget.latest ? format(manageTarget.latest) : 'None recorded'}</dd></div><div><dt>Last collection attempt</dt><dd>{manageTarget.attempted ? format(manageTarget.attempted) : 'Unavailable'}</dd></div><div><dt>Location history</dt><dd>{manageTarget.total.toLocaleString('en-AU')} reports</dd></div></dl>
        {!data.demo && <div className="management-actions"><h3>Permanent removal</h3><p className="muted">This action is available only after a recent successful discovery confirms the device has been removed from Google Find Hub.</p><button type="button" className="danger-outline" disabled={pending || !manageTarget.deletion_allowed} title={manageTarget.deletion_reason} onClick={() => { setDeleteTarget(manageTarget); setManageTarget(null); setDeleteName('') }}>Delete Device Permanently</button>{!manageTarget.deletion_allowed && <p className="muted">{manageTarget.deletion_reason}</p>}</div>}
      </section>
    </div>}
    {deleteTarget && <div className="modal-backdrop delete-backdrop" role="presentation">
      <section ref={dialogRef} tabIndex={-1} onKeyDown={event => trapDialogKey(event, () => { if (!pending) { setDeleteTarget(null); setDeleteName('') } })} className="panel delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title">
        <div className="dialog-heading"><div><p className="eyebrow">IRREVERSIBLE ACTION</p><h2 id="delete-title">Delete Device Permanently</h2></div><button type="button" className="icon-button" aria-label="Close deletion confirmation" disabled={pending} onClick={() => { setDeleteTarget(null); setDeleteName('') }}>×</button></div>
        <dl><div><dt>Device name</dt><dd>{deleteTarget.label}</dd></div>
          <div><dt>Google Find Hub device ID</dt><dd className="device-id">{deleteTarget.device}</dd></div>
          <div><dt>GPS history records to delete</dt><dd>{deleteTarget.deletion_history_count}</dd></div></dl>
          <p className="delete-warning">This permanently removes the device registration, all local GPS history, and collector state. A verified private recovery checkpoint is required first. Existing daily backups and that restricted checkpoint retain recoverable copies; restoration requires an operator.</p>
        <form onSubmit={deleteDevice}>
          <label>Type <strong>{deleteTarget.label}</strong> to confirm
            <input autoComplete="off" value={deleteName} onChange={event => setDeleteName(event.target.value)} />
          </label>
          <div className="dialog-actions"><button type="button" disabled={pending} onClick={() => { setDeleteTarget(null); setDeleteName('') }}>Cancel</button>
            <button className="danger" disabled={pending || deleteName !== deleteTarget.label}>Delete permanently</button></div>
        </form>
      </section>
    </div>}
  </div>
}

function Brand() {
  return <div className="brand"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 21s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12Z"/><circle cx="12" cy="9" r="2.4"/></svg></span><span>Find Hub <b>Tracker</b></span></div>
}

function trapDialogKey(event: React.KeyboardEvent<HTMLElement>, close: () => void) {
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
    return
  }
  if (event.key !== 'Tab') return
  const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
    'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]'
  )).filter(item => item.offsetWidth > 0 || item.offsetHeight > 0)
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function sydneyInputToIso(value: string, timezone: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value)
  if (!match) return ''
  const parts = match.slice(1).map(Number)
  const target = Date.UTC(parts[0], parts[1] - 1, parts[2], parts[3], parts[4])
  const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
  let candidate = target
  for (let attempt = 0; attempt < 3; attempt++) {
    const values = Object.fromEntries(formatter.formatToParts(new Date(candidate)).map(part => [part.type, Number(part.value)]))
    const represented = Date.UTC(values.year, values.month - 1, values.day, values.hour, values.minute)
    candidate += target - represented
  }
  const check = Object.fromEntries(formatter.formatToParts(new Date(candidate)).map(part => [part.type, Number(part.value)]))
  if (check.year !== parts[0] || check.month !== parts[1] || check.day !== parts[2] || check.hour !== parts[3] || check.minute !== parts[4]) throw new Error('That local time does not exist in Australia/Sydney because of a daylight-saving change.')
  return new Date(candidate).toISOString()
}

createRoot(document.getElementById('root')!).render(<App />)
