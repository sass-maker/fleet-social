<svelte:head>
	<title>About Fleet Social</title>
	<meta
		name="description"
		content="Fleet Social is the private publishing workspace for Fleet projects."
	/>
</svelte:head>

<main id="main-content" class="mx-auto min-h-dvh max-w-3xl px-5 py-12 text-stone-900 sm:py-20">
	<p class="text-xs font-bold tracking-widest text-stone-500 uppercase">Fleet Social</p>
	<h1 class="mt-5 text-4xl font-extrabold tracking-tight sm:text-5xl">
		Review before it goes live.
	</h1>
	<p class="mt-6 max-w-2xl text-lg leading-relaxed text-stone-600">
		Fleet Social is a private workspace for the Fleet owner. It brings project-attributed drafts,
		approval, scheduling, and delivery receipts together. A connected YouTube channel can receive an
		approved video upload; the current YouTube flow uses private visibility.
	</p>
	<div class="mt-8 flex flex-wrap gap-3">
		<a
			href="/"
			class="rounded-full bg-stone-900 px-5 py-3 text-sm font-bold text-white hover:bg-stone-700"
			>Open the private app</a
		>
		<a
			href="/privacy"
			class="rounded-full border border-stone-300 px-5 py-3 text-sm font-bold hover:bg-stone-100"
			>Privacy policy</a
		>
	</div>
	<nav
		aria-label="Legal"
		class="mt-14 flex gap-5 border-t border-stone-200 pt-6 text-sm font-medium text-stone-600"
	>
		<a class="underline hover:text-stone-900" href="/privacy">Privacy</a>
		<a class="underline hover:text-stone-900" href="/terms">Terms</a>
	</nav>
</main>
