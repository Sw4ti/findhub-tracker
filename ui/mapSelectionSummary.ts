export type SummaryDevice = { device: string; label: string }

export type MapSelectionSummary = {
  tracker: string
  timeScope: string
  count: number
}

/** Describe the active API query and the points that are actually rendered on the map. */
export function mapSelectionSummary(
  query: string,
  devices: SummaryDevice[],
  detectionCount: number,
  timeZone = 'Australia/Sydney',
): MapSelectionSummary {
  const params = new URLSearchParams(query)
  const selectedId = params.get('device')
  const selected = selectedId ? devices.find(item => item.device === selectedId) : undefined
  const tracker = selectedId ? selected?.label ?? 'Selected tracker' : 'All trackers'
  const start = params.get('start')
  const end = params.get('end')

  const format = (value: string) => {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return null
    return new Intl.DateTimeFormat('en-AU', {
      timeZone,
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(date)
  }

  const formattedStart = start ? format(start) : null
  const formattedEnd = end ? format(end) : null
  let timeScope = 'All dates'
  if (start && end) {
    timeScope = formattedStart && formattedEnd
      ? `${formattedStart} – ${formattedEnd} (Sydney time)`
      : 'Selected date/time range'
  } else if (start) {
    timeScope = formattedStart ? `From ${formattedStart} (Sydney time)` : 'From selected date/time'
  } else if (end) {
    timeScope = formattedEnd ? `Through ${formattedEnd} (Sydney time)` : 'Through selected date/time'
  }

  return { tracker, timeScope, count: Math.max(0, detectionCount) }
}
