// Meteora Dynamic Bonding Curve transaction builder for devbot.
//
// Pure builder: reads one JSON request on stdin, prints one JSON reply on stdout. It never sees a
// private key. Python supplies public keys and a recent blockhash, gets back an UNSIGNED legacy
// transaction (base64), signs it locally and sends it.
//
//   node dbc.mjs config   {rpc, blockhash, payer, config, curve}            -> {tx}
//   node dbc.mjs pool     {rpc, blockhash, payer, config, baseMint, name, symbol, uri, buyLamports} -> {tx, pool}
//   node dbc.mjs fees     {rpc, config, mints:[...]}                        -> {pools:[{mint, pool, partnerQuote, creatorQuote}]}
//   node dbc.mjs claim    {rpc, blockhash, feeClaimer, config, baseMint}    -> {tx}
import {
  ActivationType, BaseFeeMode, CollectFeeMode, DynamicBondingCurveClient, MigrationFeeOption,
  MigrationOption, TokenAuthorityOption, TokenDecimal, TokenType, U64_MAX, buildCurveWithMarketCap,
  deriveDbcPoolAddress,
} from "@meteora-ag/dynamic-bonding-curve-sdk";
import { Connection, PublicKey } from "@solana/web3.js";
import BN from "bn.js";

const NATIVE_MINT = new PublicKey("So11111111111111111111111111111111111111112");

function curveFor(c) {
  // Anti-sniper decay (startingFeeBps -> endingFeeBps over `seconds`): early snipers pay the
  // partner (us) a large fee. LP is permanently locked after graduation, so the pool can't be pulled.
  return buildCurveWithMarketCap({
    token: {
      tokenType: TokenType.SPLToken,
      tokenBaseDecimal: TokenDecimal.SIX,
      tokenQuoteDecimal: TokenDecimal.NINE,
      tokenAuthorityOption: TokenAuthorityOption.Immutable,
      totalTokenSupply: c.totalSupply,
      leftover: 0,
    },
    fee: {
      baseFeeParams: {
        baseFeeMode: BaseFeeMode.FeeSchedulerExponential,
        feeSchedulerParam: {
          startingFeeBps: c.startingFeeBps, endingFeeBps: c.endingFeeBps,
          numberOfPeriod: c.decayPeriods, totalDuration: c.decaySeconds,
        },
      },
      dynamicFeeEnabled: true,
      collectFeeMode: CollectFeeMode.QuoteToken,
      creatorTradingFeePercentage: 0, // we are both partner and creator; one claim path is enough
      poolCreationFee: 0,
      enableFirstSwapWithMinFee: true, // the dev's own first buy pays the minimum fee
    },
    migration: {
      migrationOption: MigrationOption.MET_DAMM_V2,
      migrationFeeOption: MigrationFeeOption.FixedBps25,
      migrationFee: { feePercentage: 0, creatorFeePercentage: 0 },
    },
    liquidityDistribution: {
      partnerPermanentLockedLiquidityPercentage: c.lockedLpPercent,
      partnerLiquidityPercentage: 100 - c.lockedLpPercent,
      creatorPermanentLockedLiquidityPercentage: 0,
      creatorLiquidityPercentage: 0,
    },
    lockedVesting: {
      totalLockedVestingAmount: 0, numberOfVestingPeriod: 0, cliffUnlockAmount: 0,
      totalVestingDuration: 0, cliffDurationFromMigrationTime: 0,
    },
    activationType: ActivationType.Timestamp,
    initialMarketCap: c.initialMarketCap,
    migrationMarketCap: c.migrationMarketCap,
  });
}

function poolOf(config, baseMint) {
  return deriveDbcPoolAddress(NATIVE_MINT, new PublicKey(baseMint), new PublicKey(config)).toBase58();
}

function wire(tx, req, feePayer) {
  tx.feePayer = new PublicKey(feePayer);
  tx.recentBlockhash = req.blockhash;
  return tx.serialize({ requireAllSignatures: false, verifySignatures: false }).toString("base64");
}

const handlers = {
  async config(client, req) {
    const params = curveFor(req.curve);
    const tx = await client.partner.createConfig({
      config: new PublicKey(req.config),
      feeClaimer: new PublicKey(req.payer),
      leftoverReceiver: new PublicKey(req.payer),
      quoteMint: NATIVE_MINT,
      payer: new PublicKey(req.payer),
      ...params,
    });
    return { tx: wire(tx, req, req.payer) };
  },

  async pool(client, req) {
    const payer = new PublicKey(req.payer);
    const tx = await client.creator.createPoolWithFirstBuy({
      createPoolParam: {
        baseMint: new PublicKey(req.baseMint), config: new PublicKey(req.config),
        name: req.name, symbol: req.symbol, uri: req.uri, payer, poolCreator: payer,
      },
      // Atomic with pool creation, so nobody can trade ahead of it: no minimum needed.
      firstBuyParam: req.buyLamports > 0 ? {
        buyer: payer, buyAmount: new BN(String(req.buyLamports)),
        minimumAmountOut: new BN(0), referralTokenAccount: null,
      } : undefined,
    });
    return { tx: wire(tx, req, req.payer), pool: poolOf(req.config, req.baseMint) };
  },

  async fees(client, req) {
    const out = [];
    for (const mint of req.mints) {
      const pool = poolOf(req.config, mint);
      try {
        const b = await client.state.getPoolFeeBreakdown(pool);
        out.push({
          mint, pool, partnerQuote: b.partner.unclaimedQuoteFee.toString(),
          creatorQuote: b.creator.unclaimedQuoteFee.toString(),
        });
      } catch (e) {
        out.push({ mint, pool, error: String(e.message || e) });
      }
    }
    return { pools: out };
  },

  async claim(client, req) {
    const feeClaimer = new PublicKey(req.feeClaimer);
    const tx = await client.partner.claimPartnerTradingFee({
      feeClaimer, payer: feeClaimer, pool: new PublicKey(poolOf(req.config, req.baseMint)),
      maxBaseAmount: U64_MAX, maxQuoteAmount: U64_MAX, receiver: feeClaimer,
    });
    return { tx: wire(tx, req, req.feeClaimer) };
  },
};

const cmd = process.argv[2];
if (!handlers[cmd]) {
  console.error(`unknown command ${cmd}; expected one of ${Object.keys(handlers).join(", ")}`);
  process.exit(2);
}
let raw = "";
for await (const chunk of process.stdin) raw += chunk;
const req = JSON.parse(raw);
const client = DynamicBondingCurveClient.create(new Connection(req.rpc, "confirmed"), "confirmed");
try {
  console.log(JSON.stringify(await handlers[cmd](client, req)));
} catch (e) {
  console.error(String(e.stack || e));
  process.exit(1);
}
