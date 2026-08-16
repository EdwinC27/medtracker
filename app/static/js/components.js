/* Reusable pieces of markup shared by several pages. */
(function () {
  'use strict';

  const el = UI.el;

  /* The tick / circle in front of a dose, so status reads at a glance. */
  const STATUS_MARK = {
    taken: '✓', skipped: '×', missed: '!', scheduled: '○',
    // Not a warning sign: this dose predates the medication's own registration.
    before_registration: '·',
  };

  /* One listener for the whole page closes any open snooze menu. Registering it
     per row would leak a listener every time a list is re-rendered, and Today
     re-renders itself every minute. */
  document.addEventListener('click', function () {
    document.querySelectorAll('.snooze__menu').forEach(function (menu) {
      menu.classList.add('hidden');
    });
  });

  /* A single dose line with its status and the Taken / Skipped / Snooze actions. */
  function doseRow(dose, onChanged, options) {
    const opts = options || {};
    const row = el('div', 'dose-row dose-row--' + dose.status);

    if (opts.timeline) {
      row.appendChild(el('span', 'dose-mark dose-mark--' + dose.status,
        STATUS_MARK[dose.status] || '○'));
    }
    row.appendChild(el('div', 'dose-row__time', F.time(dose.scheduled_at)));

    const main = el('div', 'dose-row__main');
    if (!opts.hideName) {
      main.appendChild(el('strong', null, dose.medication_name || ''));
    }
    const summary = UI.doseSummary(dose);
    if (summary) main.appendChild(el('div', 'card__meta', summary));
    if (opts.showDate) {
      main.appendChild(el('div', 'card__meta', F.dateLong(dose.scheduled_at)));
    }
    // When it was actually handled — the scheduled time above never moves.
    if (dose.marked_at && dose.status === 'taken') {
      main.appendChild(el('div', 'card__meta', T.t('today.taken_at', { time: F.time(dose.marked_at) })));
    } else if (dose.marked_at && dose.status === 'skipped') {
      main.appendChild(el('div', 'card__meta', T.t('today.skipped_at', { time: F.time(dose.marked_at) })));
    } else if (dose.status === 'missed' && dose.status_changed_at) {
      main.appendChild(el('div', 'card__meta', T.t('today.missed_at', { time: F.time(dose.status_changed_at) })));
    }
    // No per-row explanation for a historical dose: the badge says what it is,
    // and repeating the sentence on eighty rows is noise. The screens that list
    // them explain it once, above the list.
    if (dose.snoozed_until) {
      main.appendChild(el('div', 'card__meta snoozed',
        T.t('today.snoozed_until', { time: F.time(dose.snoozed_until) })));
    }
    row.appendChild(main);

    row.appendChild(UI.badge(dose.status));

    const actions = el('div', 'dose-row__actions');
    if (dose.status === 'taken' || dose.status === 'skipped') {
      actions.appendChild(button('actions.undo', 'btn--ghost', 'scheduled'));
    } else {
      actions.appendChild(button('actions.mark_taken', 'btn--success', 'taken'));
      actions.appendChild(button('actions.mark_skipped', 'btn--ghost', 'skipped'));
      // can_snooze comes from the server: a dose whose reminders have not
      // started yet has nothing to postpone.
      if (dose.can_snooze) actions.appendChild(snoozeButton(dose, onChanged));
    }
    row.appendChild(actions);

    function button(labelKey, variant, status) {
      const btn = el('button', 'btn btn--sm ' + variant, T.t(labelKey));
      btn.type = 'button';
      btn.addEventListener('click', async function () {
        // Taking a dose more than 30 minutes early is unusual enough to ask
        // about; anything from 30 minutes before onwards goes straight through.
        if (status === 'taken' && isEarly(dose)) {
          const ok = await UI.confirm(
            'confirm.taken_early_title', 'confirm.taken_early_body',
            { scheduled: F.time(dose.scheduled_at), now: F.time(new Date()) },
            'actions.mark_taken', false
          );
          if (!ok) return;
        }
        btn.disabled = true;
        try {
          await API.post('/api/doses/' + dose.id + '/status', { status: status });
          const messages = { taken: 'message.dose_taken', skipped: 'message.dose_skipped', scheduled: 'message.dose_reset' };
          UI.notify.success(messages[status]);
          if (onChanged) onChanged();
        } catch (err) {
          UI.notify.error(err);
          btn.disabled = false;
        }
      });
      return btn;
    }

    return row;
  }

  /* Snooze: a small menu of delays. It moves the reminder only — the dose keeps
     its scheduled time and stays pending. */
  function snoozeButton(dose, onChanged) {
    const wrap = el('span', 'snooze');
    const btn = el('button', 'btn btn--sm btn--ghost', T.t('snooze.action'));
    btn.type = 'button';
    const menu = el('div', 'snooze__menu hidden');

    ((T.settings && T.settings.snooze_options) || [10, 30, 60]).forEach(function (minutes) {
      const option = el('button', 'snooze__option', T.t('snooze.minutes_' + minutes));
      option.type = 'button';
      option.addEventListener('click', async function () {
        menu.classList.add('hidden');
        try {
          const updated = await API.post('/api/doses/' + dose.id + '/snooze', { minutes: minutes });
          UI.notify.success('snooze.done', { time: F.time(updated.snoozed_until) });
          if (onChanged) onChanged();
        } catch (err) { UI.notify.error(err); }
      });
      menu.appendChild(option);
    });

    btn.addEventListener('click', function (event) {
      event.stopPropagation();
      document.querySelectorAll('.snooze__menu').forEach(function (other) {
        if (other !== menu) other.classList.add('hidden');
      });
      menu.classList.toggle('hidden');
    });
    wrap.appendChild(btn);
    wrap.appendChild(menu);
    return wrap;
  }

  /* The threshold comes from the server (scheduled time minus 30 minutes), so
     the rule lives in one tested place instead of being re-derived here. */
  function isEarly(dose) {
    if (!dose.confirm_taken_before) return false;
    const threshold = F.parse(dose.confirm_taken_before);
    return threshold ? new Date() < threshold : false;
  }

  /* Medication card used on the dashboard and on the medications list. */
  function medicationCard(medication, handlers) {
    const card = el('article', 'card');

    const head = el('div', 'card__title');
    head.appendChild(UI.thumb(medication));
    const titleBox = el('div');
    const link = el('a', null, medication.name);
    link.href = '/medications/' + medication.id;
    link.style.textDecoration = 'none';
    link.style.color = 'inherit';
    const heading = el('h3');
    heading.appendChild(link);
    titleBox.appendChild(heading);
    titleBox.appendChild(el('div', 'card__meta', UI.doseSummary(medication)));
    head.appendChild(titleBox);
    card.appendChild(head);

    card.appendChild(el('div', 'card__meta', F.frequency(medication.frequency_hours)));

    const next = el('div', 'card__meta');
    if (medication.next_dose) {
      next.textContent = T.t('medication.next_dose') + ': ' + F.whenLabel(medication.next_dose.scheduled_at);
    } else {
      next.textContent = T.t('medication.no_next_dose');
    }
    card.appendChild(next);

    card.appendChild(el('div', 'card__meta', T.t('medication.treatment') + ': ' +
      (medication.end_date
        ? T.t('medication.treatment_range', {
            start: F.dateShort(medication.start_date),
            end: F.dateShort(medication.end_date),
          })
        : F.dateShort(medication.start_date) + ' → ' + T.t('medication.no_end_date'))));

    if (medication.comments) card.appendChild(el('div', 'card__meta', medication.comments));

    const footer = el('div', 'card__footer');
    footer.appendChild(UI.badge(medication.status));
    if (handlers && handlers.actions) {
      handlers.actions(medication).forEach(function (node) { footer.appendChild(node); });
    }
    card.appendChild(footer);
    return card;
  }

  /* The Edit / Suspend / Resume / Complete / Delete button group. */
  function medicationActions(medication, reload) {
    const buttons = [];

    if (medication.status === 'active') {
      buttons.push(action('actions.suspend', function () {
        return confirmThen('confirm.suspend_title', 'confirm.suspend_body', {}, 'actions.suspend',
          '/api/medications/' + medication.id + '/suspend', 'message.medication_suspended');
      }));
      buttons.push(action('actions.complete', function () {
        // Finishing before the planned end date is a different decision from
        // finishing on time, and says so.
        if (medication.needs_complete_confirmation) {
          const early = medication.end_date
            ? ['confirm.complete_early_title', 'confirm.complete_early_body', {
                end: F.dateLong(medication.end_date), today: F.dateLong(new Date()),
              }]
            : ['confirm.complete_early_title', 'confirm.complete_open_ended_body', {}];
          return confirmThen(early[0], early[1], early[2], 'actions.complete',
            '/api/medications/' + medication.id + '/complete', 'message.medication_completed');
        }
        return confirmThen('confirm.complete_title', 'confirm.complete_body', {}, 'actions.complete',
          '/api/medications/' + medication.id + '/complete', 'message.medication_completed');
      }));
    } else {
      buttons.push(action('actions.resume', function () {
        return confirmThen('confirm.resume_title', 'confirm.resume_body', {}, 'actions.resume',
          '/api/medications/' + medication.id + '/resume', 'message.medication_resumed', false);
      }));
    }

    buttons.push(action('common.delete', async function () {
      const ok = await UI.confirm('confirm.delete_medication_title', 'confirm.delete_medication_body',
        { name: medication.name }, 'confirm.delete_confirm_word');
      if (!ok) return;
      try {
        await API.del('/api/medications/' + medication.id);
        UI.notify.success('message.medication_deleted');
        reload();
      } catch (err) { UI.notify.error(err); }
    }, 'btn--ghost'));

    async function confirmThen(titleKey, bodyKey, params, acceptKey, url, successKey, danger) {
      const ok = await UI.confirm(titleKey, bodyKey, params, acceptKey, danger);
      if (!ok) return;
      try {
        await API.post(url);
        UI.notify.success(successKey);
        reload();
      } catch (err) { UI.notify.error(err); }
    }

    function action(labelKey, handler, variant) {
      const btn = el('button', 'btn btn--sm ' + (variant || 'btn--ghost'), T.t(labelKey));
      btn.type = 'button';
      btn.addEventListener('click', handler);
      return btn;
    }

    return buttons;
  }

  window.C = {
    doseRow: doseRow,
    snoozeButton: snoozeButton,
    medicationCard: medicationCard,
    medicationActions: medicationActions,
  };
})();
