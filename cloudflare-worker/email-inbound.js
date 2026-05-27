/**
 * Cloudflare Email Worker — ICS Extractor
 *
 * Receives forwarded Outlook calendar invites, extracts ONLY the ICS/calendar
 * part of the MIME message, and POSTs the raw bytes to the app webhook.
 *
 * Privacy: only the ICS content (no email body, no subject, no other MIME
 * parts) is forwarded. Cloudflare processes and discards the full email after
 * the Worker exits — it is not stored.
 *
 * Deployment:
 *   1. npm install  (installs postal-mime)
 *   2. wrangler deploy
 *
 * Required Worker environment variables (set via wrangler.toml or dashboard):
 *   WEBHOOK_URL     – full URL to your app's /api/inbound/email endpoint
 *   WEBHOOK_SECRET  – value sent as X-Webhook-Secret header
 *
 * Required wrangler.toml:
 *   [email]
 *   incoming_address = "notes@yourdomain.com"  (configure routing in CF dashboard)
 */

import PostalMime from "postal-mime";

export default {
  async email(message, env, ctx) {
    // Read the raw email stream into an ArrayBuffer
    const rawEmail = await new Response(message.raw).arrayBuffer();

    let icsContent = null;

    try {
      const parser = new PostalMime();
      const email = await parser.parse(rawEmail);

      // Search attachments first (most common for Outlook invites)
      for (const attachment of email.attachments || []) {
        if (
          attachment.mimeType === "text/calendar" ||
          (attachment.filename || "").toLowerCase().endsWith(".ics")
        ) {
          icsContent = attachment.content; // ArrayBuffer
          break;
        }
      }

      // Fall back to inline text/calendar parts
      if (!icsContent) {
        for (const part of email.textParts || []) {
          if (part.mimeType === "text/calendar") {
            icsContent = part.content;
            break;
          }
        }
      }
    } catch (err) {
      console.error("MIME parse error:", err);
      return;
    }

    if (!icsContent) {
      // No ICS found — not a calendar invite, ignore silently
      return;
    }

    const webhookUrl = env.WEBHOOK_URL;
    const webhookSecret = env.WEBHOOK_SECRET || "";

    if (!webhookUrl) {
      console.error("WEBHOOK_URL not configured");
      return;
    }

    try {
      const response = await fetch(webhookUrl, {
        method: "POST",
        headers: {
          "Content-Type": "text/calendar; charset=utf-8",
          "X-Webhook-Secret": webhookSecret,
        },
        body: icsContent,
      });

      if (!response.ok) {
        const body = await response.text();
        console.error(`Webhook returned ${response.status}: ${body}`);
      }
    } catch (err) {
      console.error("Webhook POST failed:", err);
    }
  },
};
