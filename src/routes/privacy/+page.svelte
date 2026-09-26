<svelte:head>
	<title>Fleet Social privacy policy</title>
	<meta
		name="description"
		content="How Fleet Social handles connected account data and publishing media."
	/>
</svelte:head>

<main id="main-content" class="mx-auto min-h-dvh max-w-3xl px-5 py-12 text-stone-900 sm:py-20">
	<a href="/about" class="text-sm font-bold text-stone-500 underline hover:text-stone-900"
		>Fleet Social</a
	>
	<h1 class="mt-5 text-4xl font-extrabold tracking-tight">Privacy policy</h1>
	<p class="mt-3 text-sm text-stone-500">Effective 26 September 2026</p>
	<div class="mt-8 space-y-8 text-[15px] leading-7 text-stone-700">
		<section>
			<h2 class="text-xl font-bold text-stone-900">Who uses this app</h2>
			<p class="mt-2">
				Fleet Social is an owner-only publishing workspace for Fleet projects. Cloudflare Access
				protects the app. The public pages explain the service; they do not open account or
				publishing access.
			</p>
		</section>
		<section>
			<h2 class="text-xl font-bold text-stone-900">Data we handle</h2>
			<p class="mt-2">
				Fleet Social stores the operator's sign-in email, session records, project-attributed
				drafts, media, schedules, connected account identifiers, and delivery receipts. It uses
				essential session cookies for authentication.
			</p>
			<p class="mt-2">
				Fleet Social uses YouTube API Services. When the operator connects YouTube, the app requests <code
					>youtube.upload</code
				>
				and <code>youtube.readonly</code>. It reads the authorized channel's ID, name, and avatar to
				identify the destination. It stores OAuth access and refresh credentials encrypted in
				Cloudflare D1. Draft MP4 files are stored in Cloudflare R2 until removed with the draft or
				account.
			</p>
		</section>
		<section>
			<h2 class="text-xl font-bold text-stone-900">How we use and share it</h2>
			<p class="mt-2">
				The app uses channel data to show which account will receive a video, refresh access when
				needed, upload an owner-approved MP4 to YouTube, and check the resulting video ID and
				visibility for a delivery receipt. Only the operator can approve a destination and upload.
				The app sends the approved video, title, description, and visibility to YouTube for
				publication. Cloudflare hosts the app, database, and media storage. Fleet Social does not
				sell Google user data or use it for advertising or model training.
			</p>
		</section>
		<section>
			<h2 class="text-xl font-bold text-stone-900">Control and deletion</h2>
			<p class="mt-2">
				The operator can disconnect a YouTube account in Accounts. This removes stored credentials
				and pending posts for that connection; an identifier and receipts for videos already
				published may remain in Posts. Drafts and their media can be deleted in the app, and
				deleting the Fleet Social account removes its remaining local data and stored media. A video
				already uploaded to YouTube remains there until removed in YouTube Studio. Google access can
				also be revoked in <a class="underline" href="https://myaccount.google.com/permissions"
					>Google Account permissions</a
				>.
			</p>
		</section>
		<section>
			<h2 class="text-xl font-bold text-stone-900">Other services and contact</h2>
			<p class="mt-2">
				Google's handling of data is described in the <a
					class="underline"
					href="https://policies.google.com/privacy">Google Privacy Policy</a
				>. For questions or a data request, contact the Fleet Social operator through the
				<a class="underline" href="https://sarthakagrawal.dev/">operator's public contact page</a>.
			</p>
		</section>
	</div>
	<nav
		aria-label="Legal"
		class="mt-14 flex gap-5 border-t border-stone-200 pt-6 text-sm font-medium text-stone-600"
	>
		<a class="underline hover:text-stone-900" href="/about">About</a>
		<a class="underline hover:text-stone-900" href="/terms">Terms</a>
	</nav>
</main>
