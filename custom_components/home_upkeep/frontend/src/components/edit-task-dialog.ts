import { LitElement, css, html, type PropertyValues } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import type { Task, TaskUpdate } from "../ha-api";
import {
  badgeStyles,
  buttonStyles,
  checkboxStyles,
  dialogStyles,
  formStyles,
  inputStyles,
} from "../styles";

const MONTHS = [
  { num: 1, name: "Jan" },
  { num: 2, name: "Feb" },
  { num: 3, name: "Mar" },
  { num: 4, name: "Apr" },
  { num: 5, name: "May" },
  { num: 6, name: "Jun" },
  { num: 7, name: "Jul" },
  { num: 8, name: "Aug" },
  { num: 9, name: "Sep" },
  { num: 10, name: "Oct" },
  { num: 11, name: "Nov" },
  { num: 12, name: "Dec" },
];

@customElement("home-upkeep-edit-task-dialog")
export class HomeUpkeepEditTaskDialog extends LitElement {
  @property({ attribute: false }) task: Task | null = null;

  @state() private _title = "";

  @state() private _description = "";

  @state() private _dueDate = "";

  @state() private _completed = false;

  @state() private _completedAt = "";

  @state() private _reschedulePeriod = "";

  @state() private _rescheduleBase: "completed" | "due" = "completed";

  @state() private _prohibitedMonths: number[] = [];

  @state() private _constraints: string[] = [];

  @state() private _constraintInput = "";

  static styles = [
    dialogStyles,
    buttonStyles,
    inputStyles,
    formStyles,
    checkboxStyles,
    badgeStyles,
    css`
      label.block {
        display: block;
      }
      .section {
        margin-top: 1rem;
      }
      .section-heading {
        margin-bottom: 0.5rem;
      }
      .months-grid {
        display: grid;
        grid-template-columns: repeat(6, 1fr);
        gap: 0.5rem;
      }
      .month-button {
        border-radius: 0.5rem;
        padding: 0.5rem 0.75rem;
        font-size: 0.75rem;
        font-weight: 500;
        transition: background-color 0.2s;
        cursor: pointer;
        border: 1px solid transparent;
      }
      .month-button.prohibited {
        border-color: var(--hu-error-line);
        background: var(--hu-error-fill);
        color: var(--hu-text);
      }
      .month-button.prohibited:hover {
        background: color-mix(in srgb, var(--hu-error) 28%, transparent);
      }
      .month-button.allowed {
        border-color: color-mix(in srgb, var(--hu-success) 40%, transparent);
        background: var(--hu-success-fill);
        color: var(--hu-text);
      }
      .month-button.allowed:hover {
        background: color-mix(in srgb, var(--hu-success) 30%, transparent);
      }
      .constraints {
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
      }
      .constraint-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
      }
      .completed-row {
        display: flex;
        align-items: center;
        gap: 0.75rem;
      }
    `,
  ];

  willUpdate(changed: PropertyValues) {
    if (changed.has("task") && this.task) {
      const task = this.task;
      this._title = task.title;
      this._description = task.description ?? "";
      this._dueDate = task.due_date ? task.due_date.slice(0, 10) : "";
      this._completed = task.completed;
      this._completedAt = task.completed_at
        ? new Date(task.completed_at).toISOString().slice(0, 16)
        : "";
      this._reschedulePeriod = task.reschedule_period ?? "";
      this._rescheduleBase = task.reschedule_base ?? "completed";
      this._prohibitedMonths = task.prohibited_months ?? [];
      this._constraints = task.constraints ?? [];
      this._constraintInput = "";
    }
  }

  private _close() {
    this.dispatchEvent(
      new CustomEvent("dialog-close", { bubbles: true, composed: true }),
    );
  }

  private _save() {
    const payload: TaskUpdate = {
      title: this._title.trim() || undefined,
      description: this._description.trim() || undefined,
      completed: this._completed,
      due_date: this._dueDate || null,
      reschedule_period: this._reschedulePeriod || null,
      reschedule_base: this._rescheduleBase,
      completed_at: this._completedAt
        ? new Date(this._completedAt).toISOString()
        : null,
      prohibited_months: this._prohibitedMonths,
      constraints: this._constraints,
    };
    this.dispatchEvent(
      new CustomEvent("dialog-save", {
        detail: { payload },
        bubbles: true,
        composed: true,
      }),
    );
  }

  private _toggleMonth(num: number) {
    this._prohibitedMonths = this._prohibitedMonths.includes(num)
      ? this._prohibitedMonths.filter((m) => m !== num)
      : [...this._prohibitedMonths, num];
  }

  private _addConstraint() {
    const value = this._constraintInput.trim();
    if (value && !this._constraints.includes(value)) {
      this._constraints = [...this._constraints, value];
    }
    this._constraintInput = "";
  }

  render() {
    if (!this.task) {
      return null;
    }
    return html`
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-task-title"
        class="dialog"
        @click=${(e: Event) => {
          if (e.target === e.currentTarget) this._close();
        }}
      >
        <div class="dialog-body-large">
          <div class="dialog-content">
            <h2 id="edit-task-title" class="dialog-title-large">Edit Task</h2>

            <div class="form-grid">
              <label class="block">
                <span class="dialog-label">Title</span>
                <input
                  class="input-field"
                  .value=${this._title}
                  @input=${(e: InputEvent) => {
                    this._title = (e.target as HTMLInputElement).value;
                  }}
                />
              </label>
              <label class="block">
                <span class="dialog-label">Description</span>
                <input
                  class="input-field"
                  .value=${this._description}
                  @input=${(e: InputEvent) => {
                    this._description = (e.target as HTMLInputElement).value;
                  }}
                />
              </label>
              <label class="block">
                <span class="dialog-label">Due date</span>
                <input
                  class="input-field"
                  type="date"
                  .value=${this._dueDate}
                  @input=${(e: InputEvent) => {
                    this._dueDate = (e.target as HTMLInputElement).value;
                  }}
                />
              </label>
            </div>

            <div class="section">
              <div class="form-grid">
                <label class="block">
                  <span class="dialog-label">Reschedule period</span>
                  <input
                    class="input-field"
                    placeholder="e.g. 5d, 1w, 1m"
                    .value=${this._reschedulePeriod}
                    @input=${(e: InputEvent) => {
                      this._reschedulePeriod = (
                        e.target as HTMLInputElement
                      ).value;
                    }}
                  />
                </label>
                <label class="block">
                  <span class="dialog-label">Reschedule from</span>
                  <select
                    class="input-field"
                    .value=${this._rescheduleBase}
                    @change=${(e: Event) => {
                      this._rescheduleBase = (e.target as HTMLSelectElement)
                        .value as "completed" | "due";
                    }}
                  >
                    <option value="completed">Completed date</option>
                    <option value="due">Due date</option>
                  </select>
                </label>
              </div>
            </div>

            <div class="section">
              <div class="section-heading">
                <span class="dialog-label-inline">Prohibited Months</span>
                <p class="dialog-help-text">
                  Toggle months when this task cannot be completed
                </p>
              </div>
              <div class="months-grid">
                ${MONTHS.map(({ num, name }) => {
                  const isProhibited = this._prohibitedMonths.includes(num);
                  return html`
                    <button
                      type="button"
                      class="month-button ${isProhibited
                        ? "prohibited"
                        : "allowed"}"
                      title=${isProhibited
                        ? `Prohibited in ${name}`
                        : `Allowed in ${name}`}
                      @click=${() => this._toggleMonth(num)}
                    >
                      ${name}
                    </button>
                  `;
                })}
              </div>
            </div>

            <div class="section">
              <div class="section-heading">
                <span class="dialog-label-inline">Constraints</span>
                <p class="dialog-help-text">
                  Add constraints for this task (press Enter or comma to add)
                </p>
              </div>
              <div class="constraints">
                <input
                  class="input-field"
                  placeholder="e.g. not raining, dry weather, weekend only"
                  .value=${this._constraintInput}
                  @input=${(e: InputEvent) => {
                    this._constraintInput = (
                      e.target as HTMLInputElement
                    ).value;
                  }}
                  @keydown=${(e: KeyboardEvent) => {
                    if (e.key === "Enter" || e.key === ",") {
                      e.preventDefault();
                      this._addConstraint();
                    }
                  }}
                />
                ${this._constraints.length
                  ? html`<div class="constraint-tags">
                      ${this._constraints.map(
                        (constraint, index) => html`
                          <span class="badge-info">
                            ${constraint}
                            <button
                              type="button"
                              aria-label="Remove constraint: ${constraint}"
                              @click=${() => {
                                this._constraints = this._constraints.filter(
                                  (_, i) => i !== index,
                                );
                              }}
                            >
                              ×
                            </button>
                          </span>
                        `,
                      )}
                    </div>`
                  : null}
              </div>
            </div>

            <div class="section completed-row">
              <label class="completed-row">
                <input
                  type="checkbox"
                  class="checkbox"
                  .checked=${this._completed}
                  @change=${(e: Event) => {
                    this._completed = (e.target as HTMLInputElement).checked;
                  }}
                />
                <span class="dialog-label-inline">Completed</span>
              </label>
            </div>

            ${this._completed
              ? html`<div class="section">
                  <label class="block">
                    <span class="dialog-label">Completed at</span>
                    <input
                      class="input-field"
                      type="datetime-local"
                      .value=${this._completedAt}
                      @input=${(e: InputEvent) => {
                        this._completedAt = (
                          e.target as HTMLInputElement
                        ).value;
                      }}
                    />
                  </label>
                </div>`
              : null}

            <div class="dialog-actions">
              <button class="btn-secondary" @click=${() => this._close()}>
                Cancel
              </button>
              <button class="btn-primary" @click=${() => this._save()}>
                Save Changes
              </button>
            </div>
          </div>
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "home-upkeep-edit-task-dialog": HomeUpkeepEditTaskDialog;
  }
}
