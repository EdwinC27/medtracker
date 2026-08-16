/* Date, time and dose formatting.
 *
 * Mirrors app/services/textformat.py and reads the same catalog data, so the
 * browser and the Windows toasts word things identically.
 *
 * Datetimes coming from the API are naive local strings ("2026-08-16T22:00:00").
 * `parse()` builds a Date from the parts explicitly so the browser never
 * applies a timezone offset to them.
 */
(function () {
  'use strict';

  function parse(value) {
    if (!value) return null;
    if (value instanceof Date) return value;
    const m = String(value).match(
      /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/
    );
    if (!m) return null;
    return new Date(
      Number(m[1]), Number(m[2]) - 1, Number(m[3]),
      Number(m[4] || 0), Number(m[5] || 0), Number(m[6] || 0)
    );
  }

  function meta() { return (T.catalog && T.catalog.meta) || { time_24h: false }; }
  function fmt() { return (T.catalog && T.catalog.format) || {}; }

  function time(value) {
    const date = parse(value);
    if (!date) return '';
    if (meta().time_24h) {
      return String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
    }
    const hour = date.getHours() % 12 || 12;
    const suffix = date.getHours() >= 12 ? fmt().pm : fmt().am;
    return hour + ':' + String(date.getMinutes()).padStart(2, '0') + ' ' + suffix;
  }

  function dateLong(value) {
    const date = parse(value);
    if (!date) return '';
    return (fmt().date_long || '{month} {day}, {year}')
      .replace('{day}', date.getDate())
      .replace('{month}', (fmt().months || [])[date.getMonth()] || '')
      .replace('{year}', date.getFullYear());
  }

  function dateShort(value) {
    const date = parse(value);
    if (!date) return '';
    return (fmt().date_short || '{month_short} {day}')
      .replace('{day}', date.getDate())
      .replace('{month_short}', (fmt().months_short || [])[date.getMonth()] || '');
  }

  function dateTime(value) {
    const date = parse(value);
    if (!date) return '';
    return (fmt().date_time || '{date} — {time}')
      .replace('{date}', dateLong(date))
      .replace('{time}', time(date));
  }

  function isSameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  /* "Today at 10:00 PM" / "Tomorrow at ..." / "August 21, 2026 at ..." */
  function whenLabel(value) {
    const date = parse(value);
    if (!date) return '';
    const now = new Date();
    const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    if (isSameDay(date, now)) return T.t('dashboard.today_at', { time: time(date) });
    if (isSameDay(date, tomorrow)) return T.t('dashboard.tomorrow_at', { time: time(date) });
    return T.t('dashboard.on_at', { date: dateLong(date), time: time(date) });
  }

  /* "in 1 h 25 min" — the countdown next to the next dose. */
  function countdown(value, reference) {
    const date = parse(value);
    if (!date) return '';
    const now = reference || new Date();
    let seconds = Math.floor((date - now) / 1000);
    if (seconds <= 0) return T.t('time.overdue');
    if (seconds < 60) return T.t('time.now');
    const days = Math.floor(seconds / 86400); seconds -= days * 86400;
    const hours = Math.floor(seconds / 3600); seconds -= hours * 3600;
    const minutes = Math.floor(seconds / 60);
    if (days > 0) return T.t('time.in_days_hours', { days: days, hours: hours });
    if (hours > 0) return T.t('time.in_hours_minutes', { hours: hours, minutes: minutes });
    return T.t('time.in_minutes', { minutes: minutes });
  }

  function daysUntil(value) {
    const date = parse(value);
    if (!date) return null;
    const now = new Date();
    const a = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const b = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.round((a - b) / 86400000);
  }

  function dose(amount, unit) {
    if (!amount) return '';
    return (amount + ' ' + T.t('unit.' + (unit || 'mg'))).trim();
  }

  function quantity(value, form) {
    const number = Number(value);
    if (!isFinite(number)) return '';
    const shown = Number.isInteger(number) ? number : number;
    const key = Math.abs(number - 1) < 1e-9 ? 'form.' + form : 'form.' + form + '_plural';
    return (shown + ' ' + T.t(key)).trim();
  }

  function frequency(hours) {
    const specific = 'frequency.every_' + hours + '_hours';
    const label = T.t(specific);
    return label === specific ? T.t('frequency.every_n_hours', { hours: hours }) : label;
  }

  /* "YYYY-MM-DD" of a Date, for <input type=date>. */
  function inputDate(date) {
    const d = date || new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function inputDateTime(value) {
    const d = parse(value);
    if (!d) return '';
    return inputDate(d) + 'T' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  window.F = {
    parse: parse,
    time: time,
    dateLong: dateLong,
    dateShort: dateShort,
    dateTime: dateTime,
    whenLabel: whenLabel,
    countdown: countdown,
    daysUntil: daysUntil,
    dose: dose,
    quantity: quantity,
    frequency: frequency,
    inputDate: inputDate,
    inputDateTime: inputDateTime,
  };
})();
