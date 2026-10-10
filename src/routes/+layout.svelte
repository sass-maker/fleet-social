<script lang="ts">
	import './layout.css';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { ChevronDown, PenLine, Settings, LogOut } from '@lucide/svelte';
	import favicon from '$lib/assets/favicon.svg';
	import { menuNav } from '$lib/components/menu-nav';
	let { children, data } = $props();
	let showWorkspaceDropdown = $state(false);
	let showProfileDropdown = $state(false);
	let workspaceTrigger: HTMLButtonElement | null = $state(null);
	let profileTrigger: HTMLButtonElement | null = $state(null);
	let logoutError = $state<string | null>(null);
	let resetBusy = $state(false);
	const isLoginRoute = $derived(page.url.pathname.startsWith('/login'));
	const userEmail = $derived(data.user?.email ?? null);
	const avatarSeed = $derived(data.displayName?.trim() || userEmail?.split('@')[0] || 'cogsend');
	const avatarSrc = $derived(
		data.profilePictureUrl ||
			(data.rehearsal
				? ''
				: `https://api.dicebear.com/7.x/notionists/svg?seed=${encodeURIComponent(avatarSeed)}`)
	);
	let failedAvatarSrc = $state<string | null>(null);
	const initials = $derived(
		avatarSeed.split(/[\s._-]+/).filter(Boolean).length >= 2
			? avatarSeed
					.split(/[\s._-]+/)
					.filter(Boolean)
					.slice(0, 2)
					.map((part) => part[0])
					.join('')
					.toUpperCase()
			: avatarSeed.slice(0, 2).toUpperCase()
	);
	const wide = $derived(
		page.url.pathname === '/' ||
			page.url.pathname === '/create' ||
			page.url.pathname.startsWith('/review/')
	);
	const links = [
		{ href: '/create', label: 'Create' },
		{ href: '/', label: 'Calendar' },
		{ href: '/?view=review', label: 'Review' },
		{ href: '/posts', label: 'Library' },
		{ href: '/accounts', label: 'Accounts' }
	];
	function activeLink(href: string) {
		return href === '/?view=review'
			? page.url.searchParams.get('view') === 'review' || page.url.pathname.startsWith('/review/')
			: href === '/'
				? page.url.pathname === '/' && page.url.searchParams.get('view') !== 'review'
				: page.url.pathname === href;
	}
	function closeOutside(event: MouseEvent) {
		const target = event.target as HTMLElement;
		if (!target.closest('.workspace-menu-container')) showWorkspaceDropdown = false;
		if (!target.closest('.profile-menu-container')) showProfileDropdown = false;
	}
	async function logout() {
		logoutError = null;
		try {
			const response = await fetch('/api/auth/logout', { method: 'POST' });
			if (!response.ok) throw new Error('Could not sign out — try again');
			if (data.authMethod === 'access') {
				window.location.assign('/cdn-cgi/access/logout');
				return;
			}
			await invalidateAll();
			await goto('/login');
		} catch (error) {
			logoutError = error instanceof Error ? error.message : 'Could not sign out';
		}
	}
	async function resetDemo() {
		resetBusy = true;
		try {
			const response = await fetch('/api/rehearsal/reset', { method: 'POST' });
			if (!response.ok) throw new Error('Could not reset demo');
			await invalidateAll();
			await goto('/');
		} catch (error) {
			logoutError = error instanceof Error ? error.message : 'Could not reset demo';
		} finally {
			resetBusy = false;
		}
	}
</script>

<svelte:head>
	<title>{data.appName} · Creator workspace</title>
	<meta
		name="description"
		content="Review, approve and schedule saved videos for connected social accounts."
	/>
	<link rel="icon" href={favicon} type="image/svg+xml" />
	<link rel="canonical" href={page.url.origin + page.url.pathname} />
	{#if !isLoginRoute && userEmail && data.studioProjectId && !data.rehearsal}
		<script
			src="https://sassmaker.com/project-strip.js"
			data-project={data.studioProjectId}
			defer
		></script>
		<script
			src="https://sassmaker.com/ai-chat-footer.js"
			data-name={data.appName}
			data-capture="false"
			defer
		></script>
	{/if}
</svelte:head>
<svelte:window
	onclick={closeOutside}
	onkeydown={(event) => {
		if (event.key === 'Escape') {
			showProfileDropdown = false;
			showWorkspaceDropdown = false;
		}
	}}
/>
<a href="#main-content" class="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4"
	>Skip to content</a
>
{#if isLoginRoute || !userEmail}
	{@render children()}
{:else}
	<div class="studio-shell">
		<header class="studio-header">
			<div class="studio-topline">
				<div class="workspace-menu-container">
					<button
						class="studio-brand"
						aria-label="Workspace menu"
						aria-haspopup="menu"
						aria-expanded={showWorkspaceDropdown}
						bind:this={workspaceTrigger}
						onclick={() => (showWorkspaceDropdown = !showWorkspaceDropdown)}
						><span class="studio-mark">f</span>{data.appName}<ChevronDown size={13} /></button
					>
					{#if showWorkspaceDropdown}<div
							class="studio-menu"
							role="menu"
							use:menuNav={{
								trigger: workspaceTrigger,
								onEscape: () => (showWorkspaceDropdown = false)
							}}
						>
							{#each [{ href: '/compose', label: 'Compose' }, { href: '/posts', label: 'Posts' }, { href: '/accounts', label: 'Accounts' }, { href: '/insights', label: 'Insights' }, { href: '/', label: 'Calendar' }] as link (link.href)}<a
									role="menuitem"
									href={link.href}
									onclick={() => (showWorkspaceDropdown = false)}>{link.label}</a
								>{/each}
						</div>{/if}
				</div>
				<div class="studio-header-actions">
					{#if data.rehearsal}<span class="rehearsal-chip">Interview rehearsal</span>{/if}
					<a href="/create" class="studio-secondary new-post"><PenLine size={14} /> New video</a>
					<div class="profile-menu-container">
						<button
							class="studio-avatar"
							aria-label="Profile menu"
							aria-haspopup="menu"
							aria-expanded={showProfileDropdown}
							bind:this={profileTrigger}
							onclick={() => (showProfileDropdown = !showProfileDropdown)}
							>{initials}{#if avatarSrc && failedAvatarSrc !== avatarSrc}<img
									src={avatarSrc}
									alt=""
									aria-hidden="true"
									referrerpolicy="no-referrer"
									onerror={() => (failedAvatarSrc = avatarSrc)}
								/>{/if}</button
						>
						{#if showProfileDropdown}<div
								class="studio-menu profile-menu"
								role="menu"
								aria-label="Profile"
								use:menuNav={{
									trigger: profileTrigger,
									onEscape: () => (showProfileDropdown = false)
								}}
							>
								<p>{data.displayName || userEmail}</p>
								<a href="/settings" role="menuitem" onclick={() => (showProfileDropdown = false)}
									><Settings size={14} /> Settings</a
								><a href="/api" role="menuitem" onclick={() => (showProfileDropdown = false)}
									>API docs</a
								><button role="menuitem" onclick={logout}><LogOut size={14} /> Log out</button
								>{#if logoutError}<p role="alert" class="studio-error" data-testid="logout-error">
										{logoutError}
									</p>{/if}
							</div>{/if}
					</div>
				</div>
			</div>
			<nav class="studio-nav" aria-label="Primary">
				{#each links as link (link.href)}<a
						href={link.href}
						class:active={activeLink(link.href)}
						aria-current={activeLink(link.href) ? 'page' : undefined}>{link.label}</a
					>{/each}
			</nav>
		</header>
		{#if data.rehearsal}<div class="rehearsal-banner">
				<span
					><strong>Local rehearsal.</strong> Sample destinations · approval and scheduling are saved locally
					· no provider uploads.</span
				><button onclick={resetDemo} disabled={resetBusy}
					>{resetBusy ? 'Resetting…' : 'Reset demo'}</button
				>
			</div>{/if}
		<main id="main-content" tabindex="-1" class:studio-wide={wide} class="studio-main">
			{@render children()}
		</main>
		<footer class="studio-legal">
			<a href="/privacy">Privacy</a><a href="/terms">Terms</a><span
				>Fleet Social · owner approval before delivery</span
			>
		</footer>
	</div>
{/if}

<style>
	.studio-shell {
		max-width: 1480px;
		margin: auto;
		padding: 28px 40px;
		min-height: 100dvh;
	}
	.studio-topline {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
	}
	.studio-brand {
		display: flex;
		align-items: center;
		gap: 11px;
		font:
			750 22px var(--font-display),
			sans-serif;
		letter-spacing: -0.7px;
		color: var(--studio-ink);
	}
	.studio-mark {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		background: var(--studio-accent);
		color: white;
		border-radius: var(--radius);
		font-size: 19px;
	}
	.studio-header-actions {
		display: flex;
		align-items: center;
		gap: 13px;
	}
	.studio-avatar {
		position: relative;
		overflow: hidden;
		width: 30px;
		height: 30px;
		border-radius: 50%;
		background: var(--secondary);
		font-size: 10px;
		font-weight: 650;
	}
	.studio-avatar img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.studio-nav {
		display: flex;
		gap: 29px;
		border-bottom: 1px solid var(--studio-line);
		margin-top: 23px;
		padding-bottom: 18px;
		font-size: 12px;
		color: var(--studio-muted);
	}
	.studio-nav a.active {
		color: var(--studio-ink);
		font-weight: 700;
	}
	.studio-main {
		max-width: 760px;
		margin: 30px auto 0;
		min-height: 60vh;
	}
	.studio-main.studio-wide {
		max-width: none;
	}
	.studio-legal {
		display: flex;
		gap: 20px;
		color: var(--studio-muted);
		font-size: 10px;
		margin-top: 44px;
		padding: 18px 0;
		border-top: 1px solid var(--studio-line);
	}
	.studio-legal span {
		margin-left: auto;
	}
	.workspace-menu-container,
	.profile-menu-container {
		position: relative;
	}
	.studio-menu {
		position: absolute;
		top: 43px;
		left: 0;
		z-index: 50;
		background: var(--studio-panel);
		border: 1px solid var(--studio-line);
		box-shadow: 0 12px 30px #253e3412;
		padding: 7px;
		border-radius: var(--radius);
		min-width: 185px;
		font-size: 12px;
	}
	.studio-menu a,
	.studio-menu button {
		display: flex;
		align-items: center;
		gap: 9px;
		padding: 10px 12px;
		width: 100%;
		border-radius: 5px;
	}
	.studio-menu a:hover,
	.studio-menu button:hover {
		background: var(--surface);
	}
	.studio-menu p {
		padding: 10px 12px;
		border-bottom: 1px solid var(--studio-line);
	}
	.profile-menu {
		left: auto;
		right: 0;
	}
	.rehearsal-chip {
		font-size: 10px;
		color: var(--studio-muted);
		border: 1px solid var(--studio-line);
		padding: 5px 9px;
		border-radius: 4px;
	}
	.rehearsal-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		background: #f1ead8;
		border-radius: var(--radius);
		padding: 10px 13px;
		font-size: 11px;
		color: #695329;
		margin-top: 17px;
	}
	.rehearsal-banner button {
		white-space: nowrap;
		text-decoration: underline;
	}
	@media (max-width: 700px) {
		.studio-shell {
			padding: 23px 18px;
		}
		.studio-brand {
			font-size: 20px;
		}
		.studio-nav {
			gap: 24px;
			overflow: auto;
		}
		.new-post,
		.rehearsal-chip {
			display: none;
		}
		.studio-main {
			margin-top: 24px;
		}
		.studio-legal {
			flex-wrap: wrap;
		}
		.studio-legal span {
			margin-left: 0;
		}
		.rehearsal-banner {
			align-items: flex-start;
			font-size: 10px;
		}
	}
</style>
