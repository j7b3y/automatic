const appStartTime = performance.now();

async function preloadImages() {
  const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const imagePromises = [];
  const num = Math.floor(10 * Math.random());
  const imageUrls = [
    `file=html/logo-bg-${dark ? 'dark' : 'light'}.jpg`,
    `file=html/logo-bg-${num}.jpg`,
  ];
  for (const url of imageUrls) {
    const img = new Image();
    const promise = new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = reject;
    });
    img.src = url;
    imagePromises.push(promise);
  }
  try {
    await Promise.all(imagePromises);
  } catch (error) {
    console.error('Error preloading images:', error);
  }
}