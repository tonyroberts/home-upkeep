import { LitElement, css, html } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import type { TaskCreate } from "../ha-api";
import { buttonStyles, inputStyles } from "../styles";

@customElement("home-upkeep-task-form")
export class HomeUpkeepTaskForm extends LitElement {
  @property({ type: Number }) listId!: number;

  /** Called with the new task payload; the form resets once it resolves. */
  @property({ attribute: false }) onSubmit!: (
    data: TaskCreate,
  ) => Promise<void>;

  /** When set, shows a Cancel button (used for the collapsible mobile form). */
  @property({ attribute: false }) onCancel?: () => void;

  @state() private _title = "";

  @state() private _description = "";

  @state() private _dueDate = "";

  @state() private _reschedulePeriod = "";

  static styles = [
    buttonStyles,
    inputStyles,
    css`
      form {
        display: grid;
        grid-template-columns: 1fr;
        gap: 0.75rem;
        border-radius: 0.5rem;
        border: 1px solid var(--hu-border);
        background: var(--hu-surface);
        padding: 1rem;
        box-shadow: var(--hu-shadow-sm);
      }
      @media (min-width: 768px) {
        form {
          grid-template-columns: repeat(5, 1fr);
        }
      }
      .actions {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 0.5rem;
      }
      @media (min-width: 768px) {
        .cancel-button {
          display: none;
        }
      }
    `,
  ];

  private _handleSubmit(e: Event) {
    e.preventDefault();
    const title = this._title.trim();
    if (!title) {
      return;
    }
    const data: TaskCreate = {
      list_id: this.listId,
      title,
      description: this._description.trim() || undefined,
      due_date: this._dueDate || undefined,
      reschedule_period: this._reschedulePeriod || undefined,
    };
    this.onSubmit(data)
      .then(() => {
        this._title = "";
        this._description = "";
        this._dueDate = "";
        this._reschedulePeriod = "";
      })
      .catch(console.error);
  }

  render() {
    return html`
      <form @submit=${(e: Event) => this._handleSubmit(e)}>
        <input
          class="input-field"
          placeholder="Task title"
          .value=${this._title}
          @input=${(e: InputEvent) => {
            this._title = (e.target as HTMLInputElement).value;
          }}
        />
        <input
          class="input-field"
          placeholder="Description (optional)"
          .value=${this._description}
          @input=${(e: InputEvent) => {
            this._description = (e.target as HTMLInputElement).value;
          }}
        />
        <input
          class="input-field"
          type="date"
          aria-label="Due date"
          .value=${this._dueDate}
          @input=${(e: InputEvent) => {
            this._dueDate = (e.target as HTMLInputElement).value;
          }}
        />
        <input
          class="input-field"
          placeholder="Reschedule (e.g. 5d, 1w, 1m)"
          aria-label="Reschedule period"
          .value=${this._reschedulePeriod}
          @input=${(e: InputEvent) => {
            this._reschedulePeriod = (e.target as HTMLInputElement).value;
          }}
        />
        <div class="actions">
          ${this.onCancel
            ? html`<button
                type="button"
                class="btn-secondary cancel-button"
                @click=${() => this.onCancel?.()}
              >
                Cancel
              </button>`
            : null}
          <button type="submit" class="btn-primary" style="width: 100%;">
            Add Task
          </button>
        </div>
      </form>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "home-upkeep-task-form": HomeUpkeepTaskForm;
  }
}
