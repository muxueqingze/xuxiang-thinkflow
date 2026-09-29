/** Production benchmark protocol hook. No prompt, tools or history changes. */
export default async function () {
  return {
    "chat.params": async (_input, output) => {
      output.maxOutputTokens = undefined;
    },
  };
}
