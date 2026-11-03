/* SecretRequestWidget — the masked form on a request_secret tool-call card.
 *
 * The design rule is IntegrationSetupWidget's, verbatim: secrets never enter
 * the model's text — this input POSTs straight to the encrypted store and
 * shows only masked status. On success the widget queues a short follow-up
 * user message so the model learns the secret is ready (the ActionNeededCard
 * resume idiom), then collapses to a "filled" row.
 */

import { useState } from 'react';
import { Check, Eye, EyeOff, KeyRound, Loader2 } from 'lucide-react';
import { api } from '@/api/client';
import { Button } from '@/components/ui/button';
import { queueWorkbenchMessage } from '@/api/workbench';
import type { SecretRequestResult } from '@/types/chat';

export function SecretRequestWidget({
  request,
  workbenchSessionId,
}: {
  request: SecretRequestResult;
  workbenchSessionId?: string;
}) {
  const [value, setValue] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [filled, setFilled] = useState(request.status === 'filled');
  const [error, setError] = useState('');

  const canSubmit = value.trim().length > 0 && !busy;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError('');
    try {
      await api.post('/api/chat-secrets/fill', {
        name: request.name,
        value: value.trim(),
      });
      setFilled(true);
      setValue('');
      // Tell the model the way every user turn arrives — never the value.
      if (workbenchSessionId) {
        await queueWorkbenchMessage(
          workbenchSessionId,
          `The secret ${request.name} has been filled and is stored. Continue what you were doing.`,
          undefined,
          'queue',
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not store the secret');
    } finally {
      setBusy(false);
    }
  };

  if (filled) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-success/25 bg-success/5 px-3 py-2 text-xs text-success-fg">
        <Check className="size-3.5" />
        <span>
          <span className="font-mono">{request.name}</span> stored — the value is in the encrypted store, never in this conversation.
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-lg border border-border/60 bg-black/10 p-3">
      <div className="flex items-center gap-2 text-xs">
        <KeyRound className="size-3.5 text-muted-foreground" />
        <span className="font-medium">
          Credential needed: <span className="font-mono">{request.name}</span>
        </span>
      </div>
      {request.description && (
        <p className="text-2xs text-muted-foreground">{request.description}</p>
      )}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <input
            type={show ? 'text' : 'password'}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void submit();
            }}
            placeholder="Paste the credential — it goes to the encrypted store, not the chat"
            className="h-8 w-full rounded-md border border-border/60 bg-card/60 px-2.5 pr-8 text-xs outline-none focus:border-primary"
            autoComplete="off"
            aria-label={`Credential for ${request.name}`}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setShow((v) => !v)}
            className="absolute right-1 top-0.5 size-7 p-1"
            aria-label={show ? 'Hide value' : 'Show value'}
          >
            {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </Button>
        </div>
        <Button type="button" onClick={() => void submit()} disabled={!canSubmit} className="h-8 shrink-0">
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : 'Store'}
        </Button>
      </div>
      {error && <p className="text-2xs text-destructive">{error}</p>}
      <p className="text-2xs text-muted-foreground">
        Referenced as <span className="font-mono">${'{' + request.name + '}'}</span> in MCP server env.
      </p>
    </div>
  );
}
