// SPDX-License-Identifier: GPL-2.0
/* Single-hart VexRiscv supervisor external interrupt CSR fanout. */
#include <linux/irqchip.h>
#include <linux/irqchip/chained_irq.h>
#include <linux/irqdomain.h>
#include <linux/of_irq.h>
#include <asm/csr.h>

static void mini_mask(struct irq_data *d) { csr_clear(0x9c0, BIT(d->hwirq)); }
static void mini_unmask(struct irq_data *d) { csr_set(0x9c0, BIT(d->hwirq)); }
static struct irq_chip mini_chip = {
	.name = "mini-csr", .irq_mask = mini_mask, .irq_unmask = mini_unmask,
};
static int mini_map(struct irq_domain *d, unsigned int irq, irq_hw_number_t hw)
{
	irq_set_chip_and_handler(irq, &mini_chip, handle_level_irq);
	irq_set_status_flags(irq, IRQ_LEVEL);
	return 0;
}
static const struct irq_domain_ops mini_ops = {
	.map = mini_map, .xlate = irq_domain_xlate_onecell,
};
static void mini_handle(struct irq_desc *desc)
{
	struct irq_domain *domain = irq_desc_get_handler_data(desc);
	struct irq_chip *chip = irq_desc_get_chip(desc);
	unsigned long pending;
	chained_irq_enter(chip, desc);
	/* Snapshot each level once; the device driver acknowledges its source. */
	pending = csr_read(0xdc0) & csr_read(0x9c0);
	while (pending) {
		unsigned int bit = __ffs(pending);
		if (generic_handle_domain_irq(domain, bit))
			csr_clear(0x9c0, BIT(bit));
		pending &= ~BIT(bit);
	}
	chained_irq_exit(chip, desc);
}
static int __init mini_irq_init(struct device_node *node, struct device_node *parent)
{
	struct irq_domain *domain;
	u32 mask, pending;
	unsigned int irq;
	if (of_property_read_u32(node, "riscv-mini,mask-csr", &mask) ||
	    of_property_read_u32(node, "riscv-mini,pending-csr", &pending) ||
	    mask != 0x9c0 || pending != 0xdc0)
		return -EINVAL;
	csr_write(0x9c0, 0);
	irq = irq_of_parse_and_map(node, 0);
	if (!irq) return -EINVAL;
	domain = irq_domain_add_linear(node, 32, &mini_ops, NULL);
	if (!domain) { irq_dispose_mapping(irq); return -ENOMEM; }
	irq_set_chained_handler_and_data(irq, mini_handle, domain);
	pr_info("riscv-mini: 32 supervisor external interrupts\n");
	return 0;
}
IRQCHIP_DECLARE(riscv_mini, "riscv-mini,vexriscv-supervisor-irq", mini_irq_init);
