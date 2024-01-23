let logMonitorEl = null;
let logMonitorStatus = true;

async function logMonitor() {
  if (logMonitorStatus) setTimeout(logMonitor, opts.logmonitor_refresh_period);
  if (!opts.logmonitor_show) return;
  logMonitorStatus = false;
  let res;
  try { res = await fetch('/sdapi/v1/log?clear=True'); } catch {}
  if (res?.ok) {
    logMonitorStatus = true;
    if (!logMonitorEl) {
      logMonitorEl = document.getElementById('logMonitorData');
      logMonitorEl.onscrollend = () => {
        const at_bottom = logMonitorEl.scrollHeight <= (logMonitorEl.scrollTop + logMonitorEl.clientHeight);
        if (at_bottom) logMonitorEl.parentElement.style = '';
      };
    }
    if (!logMonitorEl) return;
    const at_bottom = logMonitorEl.scrollHeight <= (logMonitorEl.scrollTop + logMonitorEl.clientHeight);
    const lines = await res.json();
    if (logMonitorEl && lines?.length > 0) logMonitorEl.parentElement.parentElement.style.display = opts.logmonitor_show ? 'block' : 'none';
    for (const line of lines) {
      try {
        const l = JSON.parse(line);
        const row = document.createElement('tr');
        row.style = 'padding: 10px; margin: 0;';
        row.innerHTML = `<td>${new Date(1000 * l.created).toISOString()}</td><td>${l.level}</td><td>${l.facility}</td><td>${l.module}</td><td>${l.msg}</td>`;
        logMonitorEl.appendChild(row);
      } catch {}
    }
    while (logMonitorEl.childElementCount > 100) logMonitorEl.removeChild(logMonitorEl.firstChild);
    if (at_bottom) logMonitorEl.scrollTop = logMonitorEl.scrollHeight;
    else if (lines?.length > 0) logMonitorEl.parentElement.style = 'border-bottom: 2px solid var(--highlight-color);';
  }
}

let logMonitorInitialized = false;

async function initLogMonitor() {
  if (logMonitorInitialized) return;
  const el = document.getElementsByTagName('footer')[0];
  if (!el) return;
  logMonitorInitialized = true;
  el.classList.add('log-monitor');
  el.innerHTML = `
    <table id="logMonitor" style="width: 100%;">
      <thead style="display: block; text-align: left; border-bottom: solid 1px var(--button-primary-border-color)">
        <tr>
          <th style="width: 160px">Time</th>
          <th>Level</th>
          <th style="width: 72px">Facility</th>
          <th style="width: 124px">Module</th>
          <th>Message</th>
        </tr>
      </thead>
      <tbody id="logMonitorData" style="white-space: nowrap; height: 10vh; width: 100vw; display: block; overflow-x: hidden; overflow-y: scroll">
      </tbody>
    </table>
  `;
  el.style.display = 'none';
  fetch(`/sdapi/v1/start?agent=${encodeURI(navigator.userAgent)}`);
  logMonitor();
  log('initLogMonitor');
}

onAfterUiUpdate(initLogMonitor);
