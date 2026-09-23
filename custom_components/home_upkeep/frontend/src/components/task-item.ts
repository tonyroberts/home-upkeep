import { mdiPencil, mdiSleep, mdiTrashCanOutline } from "@mdi/js";
import { LitElement, css, html } from "lit";
import { customElement, property } from "lit/decorators.js";

import type { Task } from "../ha-api";
import { parseDueDate } from "../dates";
import { icon } from "../icon";
import {
  badgeStyles,
  checkboxStyles,
  iconButtonStyles,
  taskItemStyles,
} from "../styles";

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** Warning badge text for a task's seasonal `prohibited_months`, or null. */
function prohibitedMonthWarning(task: Task): string | null {
  if (task.completed || !task.prohibited_months?.length) {
    return null;
  }

  const currentMonth = new Date().getMonth() + 1;
  const isCurrentMonthProhibited =
    task.prohibited_months.includes(currentMonth);
  const nextMonth = currentMonth === 12 ? 1 : currentMonth + 1;
  const isNextMonthProhibited = task.prohibited_months.includes(nextMonth);

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const isTaskDue = !task.due_date || parseDueDate(task.due_date) <= today;

  const isDueDateInCurrentMonth = task.due_date
    ? (() => {
        const dueDate = parseDueDate(task.due_date!);
        const currentDate = new Date();
        return (
          dueDate.getMonth() === currentDate.getMonth() &&
          dueDate.getFullYear() === currentDate.getFullYear()
        );
      })()
    : false;

  let isDueDateMonthProhibited = false;
  let dueDateMonthName = "";
  if (task.due_date) {
    const dueDate = parseDueDate(task.due_date);
    const dueDateMonth = dueDate.getMonth() + 1;
    isDueDateMonthProhibited = task.prohibited_months.includes(dueDateMonth);
    dueDateMonthName = MONTH_NAMES[dueDateMonth - 1] ?? "";
  }

  if (isCurrentMonthProhibited && (isTaskDue || isDueDateInCurrentMonth)) {
    return `Not allowed in ${MONTH_NAMES[currentMonth - 1]}`;
  }
  if (isNextMonthProhibited && (isTaskDue || isDueDateInCurrentMonth)) {
    return `Do before ${MONTH_NAMES[nextMonth - 1]}`;
  }
  if (isDueDateMonthProhibited) {
    return `Not allowed in ${dueDateMonthName}`;
  }
  return null;
}

@customElement("home-upkeep-task-item")
export class HomeUpkeepTaskItem extends LitElement {
  @property({ attribute: false }) task!: Task;

  static styles = [
    taskItemStyles,
    badgeStyles,
    checkboxStyles,
    iconButtonStyles,
    css`
      .row {
        display: flex;
        align-items: flex-start;
        gap: 0.75rem;
      }
      .checkbox {
        margin-top: 0;
      }
      .body {
        min-width: 0;
        flex: 1;
      }
      .title {
        font-size: 0.875rem;
        font-weight: 500;
        line-height: 1rem;
        color: var(--hu-text);
        margin: 0;
      }
      .title.completed {
        color: var(--hu-text-muted);
        text-decoration: line-through;
      }
      .description {
        margin: 0.25rem 0 0;
        font-size: 0.875rem;
        color: var(--hu-text-muted);
      }
      .badges {
        margin-top: 0.25rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        flex-wrap: wrap;
        font-size: 0.75rem;
        color: var(--hu-text-muted);
      }
      .actions {
        display: flex;
        gap: 0.5rem;
      }
    `,
  ];

  private _fire(name: string) {
    this.dispatchEvent(
      new CustomEvent(name, {
        detail: { task: this.task },
        bubbles: true,
        composed: true,
      }),
    );
  }

  render() {
    const task = this.task;
    const shouldShowSnooze =
      !task.completed &&
      (!task.due_date || parseDueDate(task.due_date) <= new Date());
    const warning = prohibitedMonthWarning(task);

    return html`
      <div class="task-item">
        <div class="row">
          <input
            type="checkbox"
            class="checkbox"
            .checked=${task.completed}
            @change=${() => this._fire("task-toggle")}
          />
          <div class="body">
            <h3 class="title ${task.completed ? "completed" : ""}">
              ${task.title}
            </h3>
            ${task.description
              ? html`<p class="description">${task.description}</p>`
              : null}
            <div class="badges">
              ${task.due_date
                ? html`<span class="badge-orange"
                    >Due
                    ${parseDueDate(task.due_date).toLocaleDateString()}</span
                  >`
                : null}
              ${task.completed_at
                ? html`<span class="badge-green"
                    >Completed
                    ${new Date(task.completed_at).toLocaleDateString()}</span
                  >`
                : null}
              ${warning
                ? html`<span class="badge-warning"
                    ><span>⚠️</span>${warning}</span
                  >`
                : null}
              ${task.constraints?.length
                ? [...task.constraints]
                    .sort()
                    .map(
                      (constraint) =>
                        html`<span class="badge-warning"
                          ><span>⚠️</span>${constraint}</span
                        >`,
                    )
                : null}
              ${task.reschedule_period
                ? html`<span class="badge-info"
                    ><span>⟳</span>${task.reschedule_period}</span
                  >`
                : null}
            </div>
          </div>
          <div class="actions">
            ${shouldShowSnooze
              ? html`<button
                  class="icon-button"
                  title="Snooze task"
                  aria-label="Snooze ${task.title}"
                  @click=${() => this._fire("task-snooze")}
                >
                  ${icon(mdiSleep)}
                </button>`
              : null}
            <button
              class="icon-button"
              aria-label="Edit ${task.title}"
              @click=${() => this._fire("task-edit")}
            >
              ${icon(mdiPencil)}
            </button>
            <button
              class="icon-button-danger"
              aria-label="Delete ${task.title}"
              @click=${() => this._fire("task-delete")}
            >
              ${icon(mdiTrashCanOutline)}
            </button>
          </div>
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "home-upkeep-task-item": HomeUpkeepTaskItem;
  }
}
