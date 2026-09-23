import { LitElement, css, html, type PropertyValues } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import { buttonStyles, dialogStyles, inputStyles } from "../styles";

@customElement("home-upkeep-snooze-dialog")
export class HomeUpkeepSnoozeDialog extends LitElement {
  @property({ type: Boolean }) open = false;

  @property({ attribute: false }) taskTitle = "";

  @state() private _period = "";

  static styles = [
    dialogStyles,
    buttonStyles,
    inputStyles,
    css`
      label {
        display: block;
      }
      p.hint {
        margin: 0 0 1rem;
        font-size: 0.875rem;
        color: var(--hu-text-muted);
      }
    `,
  ];

  willUpdate(changed: PropertyValues) {
    if (changed.has("open") && this.open) {
      this._period = "";
    }
  }

  private _close() {
    this.dispatchEvent(
      new CustomEvent("dialog-close", { bubbles: true, composed: true }),
    );
  }

  private _snooze() {
    const period = this._period.trim();
    if (!period) {
      return;
    }
    this.dispatchEvent(
      new CustomEvent("dialog-save", {
        detail: { period },
        bubbles: true,
        composed: true,
      }),
    );
  }

  render() {
    if (!this.open) {
      return null;
    }
    return html`
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="snooze-task-title"
        class="dialog"
        @click=${(e: Event) => {
          if (e.target === e.currentTarget) this._close();
        }}
      >
        <div class="dialog-body">
          <div class="dialog-content">
            <h2 id="snooze-task-title" class="dialog-title">Snooze Task</h2>
            <p class="hint">Snooze "${this.taskTitle}" for how long?</p>
            <div style="margin-bottom: 1.5rem;">
              <label>
                <span class="dialog-label">Snooze period</span>
                <input
                  class="input-field"
                  placeholder="e.g. 1d, 2w, 1m"
                  autofocus
                  .value=${this._period}
                  @input=${(e: InputEvent) => {
                    this._period = (e.target as HTMLInputElement).value;
                  }}
                  @keydown=${(e: KeyboardEvent) => {
                    if (e.key === "Enter") this._snooze();
                  }}
                />
                <p class="dialog-help-text">
                  Use d for days, w for weeks, m for months (e.g. 1d, 2w, 1m)
                </p>
              </label>
            </div>
            <div class="dialog-actions-no-margin">
              <button class="btn-secondary" @click=${() => this._close()}>
                Cancel
              </button>
              <button
                class="btn-primary"
                ?disabled=${!this._period.trim()}
                @click=${() => this._snooze()}
              >
                Snooze
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
    "home-upkeep-snooze-dialog": HomeUpkeepSnoozeDialog;
  }
}
