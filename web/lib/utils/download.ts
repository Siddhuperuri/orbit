/**
 * Starts a download from a URL the API just minted.
 *
 * A hidden link, clicked, rather than `window.location.assign`: the URL points at object
 * storage and answers with `Content-Disposition: attachment`, so the browser saves it
 * and the page stays where it is. Navigating the window instead would, if storage were
 * unreachable, replace the application with a browser error page and throw away
 * whatever the user had open.
 *
 * `noopener` because the target is another origin.
 */
export function startDownload(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
}
