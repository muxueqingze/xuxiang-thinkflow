/** Production benchmark protocol hook. No prompt, tools or history changes. */
export default function (pi) {
  pi.on("before_provider_request", (event) => {
    const payload = { ...event.payload };
    delete payload.max_tokens;
    delete payload.max_completion_tokens;
    return payload;
  });
}
